"""Local speech-to-text with faster-whisper.

faster-whisper is optional: the core runs without it, and install-voice.bat
adds it to a .venv inside the project. The model is downloaded once (from
Hugging Face) into the data folder, then everything runs offline.

Default is CPU with int8: on a 6GB GPU the VRAM is better left to Ollama.
On a GPU (install-voice-gpu.bat adds NVIDIA's cuBLAS and cuDNN to the .venv)
large-v3-turbo answers in well under a second; if the GPU cannot be used, the
model falls back to the CPU instead of failing.

Languages: "auto" means Arabic or English. Whisper's own detection can pick a
neighbouring language (Persian, Urdu...) for short Gulf-Arabic clips, which
turns the text into nonsense, so anything else is re-read as the more likely
of the two.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .audio import Clip, read_wav
from .wake import classify_reply, is_noise, split_wake

log = logging.getLogger("yousef.voice")

# approximate download sizes in MB, shown in the dashboard
MODEL_SIZES = {"tiny": 75, "base": 145, "small": 485, "medium": 1530, "large-v3-turbo": 1620}
BILINGUAL = ("ar", "en")
DECODE = {"beam_size": 5, "vad_filter": True, "vad_parameters": {"min_silence_duration_ms": 500},
          "condition_on_previous_text": False, "without_timestamps": True}


def prompts(wake_words: list[str]) -> dict:
    """Text Whisper reads as "what came before": it sets the script, the punctuation and the name's spelling."""
    arabic = next((w for w in wake_words if any("\u0600" <= c <= "\u06ff" for c in w)), "يوسف")
    latin = next((w for w in wake_words if w.isascii()), "Yousef")
    return {
        "ar": f"يا {arabic.removeprefix('يا ').strip()}، اكتب منشورًا لإنستغرام عن العرض الجديد في المتجر.",
        "en": f"{latin}, write an Instagram post about the new offer in the store.",
        "auto": f"{arabic}، {latin}.",
    }


def add_nvidia_dlls() -> list[str]:
    """Windows: make the CUDA libraries installed by install-voice-gpu.bat findable."""
    if sys.platform != "win32":
        return []
    spec = importlib.util.find_spec("nvidia")
    added = []
    for root in (spec.submodule_search_locations or []) if spec else []:
        for pkg in ("cublas", "cudnn"):
            folder = Path(root) / pkg / "bin"
            if folder.is_dir():
                os.add_dll_directory(str(folder))
                os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
                added.append(str(folder))
    return added


class VoiceNotReady(RuntimeError):
    """Model not loaded yet (or failed to load)."""


class VoiceNotInstalled(RuntimeError):
    pass


def whisper_installed() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


class WhisperBackend:
    def __init__(self, model: str, device: str, download_root: Path):
        from faster_whisper import WhisperModel  # optional dependency

        compute = "int8_float16" if device == "cuda" else "int8"
        if device == "cuda":
            add_nvidia_dlls()
        threads = max(1, (os.cpu_count() or 4) // 2)
        self.model = WhisperModel(model, device=device, compute_type=compute,
                                  cpu_threads=threads, download_root=str(download_root))

    def transcribe(self, clip: Clip, language: str | None, prompt: str,
                   prompts: dict | None = None) -> tuple[str, str]:
        """language None = Arabic or English, whichever the audio is closer to."""
        import numpy as np

        audio = np.frombuffer(clip.pcm, dtype=np.int16).astype(np.float32) / 32768.0
        if clip.rate != 16000:
            n = int(len(audio) * 16000 / clip.rate)
            audio = np.interp(np.linspace(0, len(audio), n, endpoint=False),
                              np.arange(len(audio)), audio).astype(np.float32)
        segments, info = self.model.transcribe(audio, language=language, initial_prompt=prompt, **DECODE)
        detected = getattr(info, "language", None) or language or ""
        if language is None and detected not in BILINGUAL:
            # detection is decided before any text is decoded, so re-reading costs one more pass only here
            probs = dict(getattr(info, "all_language_probs", None) or [])
            choice = "ar" if probs.get("ar", 0.0) >= probs.get("en", 0.0) else "en"
            log.info("heard %s (%.2f); reading it as %s", detected, getattr(info, "language_probability", 0.0), choice)
            segments, info = self.model.transcribe(audio, language=choice,
                                                   initial_prompt=(prompts or {}).get(choice, prompt), **DECODE)
            detected = choice
        parts = []
        for seg in segments:
            if getattr(seg, "no_speech_prob", 0.0) > 0.6 and getattr(seg, "avg_logprob", 0.0) < -1.0:
                continue
            parts.append(seg.text)
        return "".join(parts).strip(), detected


class VoiceEngine:
    def __init__(self, settings, data_dir: Path, backend_factory=None):
        self.settings = settings
        self.download_root = Path(data_dir) / "whisper"
        self._factory = backend_factory
        self._lock = threading.Lock()  # guards state below
        self._run_lock = threading.Lock()  # one transcription at a time
        self._backend = None
        self._loaded_key: tuple[str, str] | None = None
        self._state = "idle"
        self._error = ""
        self._note = ""          # e.g. the GPU could not be used, so the CPU is
        self._device_used = ""
        self._thread: threading.Thread | None = None

    @property
    def installed(self) -> bool:
        return self._factory is not None or whisper_installed()

    def _key(self) -> tuple[str, str]:
        v = self.settings.get()["voice"]
        return v["model"], v["device"]

    def status(self) -> dict:
        v = self.settings.get()["voice"]
        with self._lock:
            state = self._state
            if not self.installed:
                state = "not_installed"
            elif state == "ready" and self._loaded_key != self._key():
                state = "idle"  # settings changed; needs reload
            return {
                "installed": self.installed,
                "state": state,
                "error": self._error,
                "note": self._note,
                "device_used": self._device_used,
                "model": v["model"],
                "device": v["device"],
                "language": v["language"],
                "wake_words": v["wake_words"],
                "model_sizes": MODEL_SIZES,
            }

    def load(self) -> dict:
        """Start loading the model in the background (no-op if already loading or loaded)."""
        if not self.installed:
            raise VoiceNotInstalled("install-voice.bat")
        key = self._key()
        with self._lock:
            busy = self._state == "loading" or (self._state == "ready" and self._loaded_key == key)
            if not busy:
                self._state = "loading"
                self._error = ""
                self._thread = threading.Thread(target=self._load, args=(key,), name="voice-load", daemon=True)
                self._thread.start()
        return self.status()

    def wait_loaded(self, timeout: float = 30) -> None:
        thread = self._thread
        if thread:
            thread.join(timeout)

    def _load(self, key: tuple[str, str]) -> None:
        model, device = key
        started = time.time()
        note, used = "", device
        factory = self._factory or WhisperBackend
        try:
            self.download_root.mkdir(parents=True, exist_ok=True)
            try:
                backend = factory(model, device, self.download_root)
            except Exception as gpu_error:
                if device != "cuda":
                    raise
                # missing cuDNN/cuBLAS or not enough video memory: keep voice working on the CPU
                log.warning("voice model could not use the GPU (%s); loading on the CPU", gpu_error)
                backend = factory(model, "cpu", self.download_root)
                used = "cpu"
                note = ("تعذّر استخدام كرت الشاشة، فيعمل النموذج على المعالج. شغّل install-voice-gpu.bat "
                        f"لتثبيت مكتبات NVIDIA. ({str(gpu_error)[:160]})")
        except Exception as e:  # download failure, bad model name...
            log.exception("voice model failed to load")
            with self._lock:
                self._state = "error"
                self._error = str(e)[:400]
            return
        with self._lock:
            self._backend = backend
            self._loaded_key = key
            self._state = "ready"
            self._note = note
            self._device_used = used
        log.info("voice model %s on %s loaded in %.1fs", model, used, time.time() - started)

    def transcribe(self, wav: bytes) -> dict:
        clip = read_wav(wav)
        if not self.installed:
            raise VoiceNotInstalled("install-voice.bat")
        status = self.status()
        if status["state"] != "ready":
            if status["state"] in ("idle",):
                self.load()
            if status["state"] == "error":
                raise VoiceNotReady(f"تعذّر تحميل نموذج الصوت: {status['error']}")
            raise VoiceNotReady("نموذج الصوت يُحمَّل الآن. قد يستغرق التنزيل الأول بضع دقائق.")
        v = self.settings.get()["voice"]
        language = None if v["language"] == "auto" else v["language"]
        texts = prompts(v["wake_words"])
        started = time.time()
        with self._run_lock:
            text, detected = self._backend.transcribe(clip, language, texts[language or "auto"], texts)
        took = time.time() - started
        noise = is_noise(text)
        if noise:
            text = ""
        woke, command = split_wake(text, v["wake_words"])
        log.info("transcribed %.1fs of audio in %.1fs (%s)", clip.seconds, took, detected)
        return {
            "text": text,
            "language": detected,
            "seconds": round(clip.seconds, 2),
            "took": round(took, 2),
            "noise": noise,
            "wake": woke,
            "command": command,
            "reply": classify_reply(text),
        }
