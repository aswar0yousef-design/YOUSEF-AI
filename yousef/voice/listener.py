"""Listening from Windows itself, without a browser tab: wake word, command, spoken reply.

The microphone is read with the optional `sounddevice` package (installed by
install-voice.bat). Speech is cut into utterances by loudness, like the
dashboard does, and each one goes to the same local Whisper model. Replies
are spoken with Windows' own voices (System.Speech), and approvals work by
voice: "وافق" or "ارفض". Nothing is sent anywhere but the local model.
"""

from __future__ import annotations

import base64
import logging
import math
import queue
import re
import subprocess
import threading
import time
from array import array

from .audio import write_wav
from .engine import VoiceNotInstalled, VoiceNotReady

log = logging.getLogger("yousef.listener")

RATE = 16000
FRAME = 320               # 20 ms
COMMAND_SECONDS = 8       # after the wake word alone, how long to wait for the command
APPROVAL_SECONDS = 25
TASK_TIMEOUT = 900
DONE = ("done", "failed", "cancelled", "interrupted")


class Segmenter:
    """Loudness-based utterance cutting, the same rules as the dashboard's (voice.js)."""

    def __init__(self, on_segment):
        self.on_segment = on_segment
        self.noise = 0.004
        self.hang = 40        # 800 ms of quiet ends an utterance
        self.max_frames = 1000
        self.pre_roll = 15    # keep 300 ms before speech
        self.reset()

    def reset(self) -> None:
        self.ring: list[bytes] = []
        self.frames: list[bytes] = []
        self.speaking = False
        self.loud = self.quiet = self.voiced = 0

    @staticmethod
    def rms(frame: bytes) -> float:
        samples = array("h", frame)
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768

    def push(self, frame: bytes) -> None:
        level = self.rms(frame)
        threshold = max(self.noise * 3, 0.012)
        if not self.speaking:
            self.ring.append(frame)
            if len(self.ring) > self.pre_roll:
                self.ring.pop(0)
            if level > threshold:
                self.loud += 1
                if self.loud >= 3:
                    self.speaking, self.frames, self.ring = True, self.ring[:], []
                    self.quiet, self.voiced = 0, self.loud
            else:
                self.loud = 0
                self.noise = min(0.05, max(0.001, self.noise * 0.95 + level * 0.05))
            return
        self.frames.append(frame)
        if level > threshold * 0.7:
            self.quiet = 0
            self.voiced += 1
        else:
            self.quiet += 1
        if self.quiet >= self.hang or len(self.frames) >= self.max_frames:
            frames, voiced = self.frames, self.voiced
            self.reset()
            if voiced >= 10:
                self.on_segment(b"".join(frames))


def speakable(text: str, limit: int = 450) -> str:
    """The spoken opening of a reply: no code, no markdown symbols, a few sentences."""
    text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    first = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0]
    first = re.sub(r"^[#>\-*•\d.\s|]+", "", first, flags=re.M)
    first = re.sub(r"[*_#|>]+", " ", first)
    first = " ".join(first.split())
    if len(first) > limit:
        cut = max(first.rfind(".", 0, limit), first.rfind("،", 0, limit), first.rfind("؟", 0, limit))
        first = first[:cut + 1] if cut > limit // 3 else first[:limit]
    return first


def is_arabic(text: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", text or ""))


class WindowsSpeaker:
    """Windows' built-in voices through PowerShell's System.Speech; the text goes in on stdin."""

    SCRIPT = """[Console]::InputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | Where-Object {{ $_.Enabled -and $_.VoiceInfo.Culture.Name.StartsWith('{lang}') }} | Select-Object -First 1
if ($v) {{ $s.SelectVoice($v.VoiceInfo.Name) }} elseif ('{lang}' -eq 'ar') {{ exit 3 }}
$s.Rate = 0
$s.Speak([Console]::In.ReadToEnd())"""

    def __init__(self):
        self.missing_arabic = False
        self._proc: subprocess.Popen | None = None

    def speak(self, text: str) -> bool:
        lang = "ar" if is_arabic(text) else "en"
        if lang == "ar" and self.missing_arabic:
            return False
        script = self.SCRIPT.format(lang=lang)  # lang is one of two fixed values
        encoded = base64.b64encode(script.encode("utf-16-le")).decode()
        try:
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._proc.communicate(text.encode("utf-8"), timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("speech failed: %s", e)
            self.stop()
            return False
        if self._proc.returncode == 3:
            self.missing_arabic = True
            log.info("no Arabic Windows voice installed; replies are shown in the dashboard only")
            return False
        return self._proc.returncode == 0

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()


class SoundDeviceMic:
    """16 kHz mono 16-bit microphone frames from the default input device."""

    def __init__(self):
        import sounddevice  # optional: installed by install-voice.bat
        self.sd = sounddevice
        self.stream = None

    def start(self, on_frame) -> None:
        def callback(indata, frames, when, status):
            on_frame(bytes(indata))

        self.stream = self.sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                             callback=callback)
        self.stream.start()

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def mic_available() -> bool:
    try:
        import sounddevice  # noqa: F401
        return True
    except (ImportError, OSError):
        return False


class NativeListener:
    def __init__(self, settings, engine, orchestrator, memory, mic_factory=None, speaker=None, clock=time.monotonic):
        self.settings = settings
        self.engine = engine            # VoiceEngine (the same local Whisper model)
        self.orchestrator = orchestrator
        self.memory = memory
        self.mic_factory = mic_factory or SoundDeviceMic
        self.speaker = speaker or WindowsSpeaker()
        self.clock = clock
        self.mode = "wake"              # wake | command | approval
        self.until = 0.0
        self.pending: dict | None = None  # {"task_id", "step_id"} waiting for a spoken approval
        self.heard = ""                 # the last thing understood, for the dashboard
        self.error = ""
        self._mic = None
        self._segments: queue.Queue = queue.Queue(maxsize=4)
        self._speaking = threading.Event()
        self._say_lock = threading.Lock()  # one sentence at a time
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---- control ------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._mic is not None

    def start(self) -> None:
        with self._lock:
            if self._mic is not None:
                return
            if not self.engine.installed:
                raise VoiceNotInstalled("install-voice.bat")
            self.engine.load()
            try:
                mic = self.mic_factory()
            except (ImportError, OSError) as e:
                self.error = "الاستماع من النظام يحتاج sounddevice: شغّل install-voice.bat مرة أخرى"
                raise VoiceNotInstalled(self.error) from e
            self._stop.clear()
            self.mode, self.pending, self.error = "wake", None, ""
            seg = Segmenter(self._on_segment)

            def on_frame(frame: bytes):
                if self._speaking.is_set():
                    seg.reset()  # never hear Yousef's own voice
                    return
                seg.push(frame)

            try:
                mic.start(on_frame)
            except Exception as e:  # no microphone, or Windows privacy settings block it
                self.error = f"تعذّر فتح الميكروفون: {e}"
                raise VoiceNotReady(self.error) from None
            self._mic = mic
            self._worker = threading.Thread(target=self._work, name="yousef-listener", daemon=True)
            self._worker.start()
            log.info("listening from Windows")

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            if self._mic is not None:
                try:
                    self._mic.stop()
                finally:
                    self._mic = None
            self.speaker.stop()

    def status(self) -> dict:
        return {"running": self.running, "mode": self.mode, "heard": self.heard, "error": self.error,
                "mic": mic_available(), "arabic_voice": not getattr(self.speaker, "missing_arabic", False)}

    # ---- audio --------------------------------------------------------------------------
    def _on_segment(self, pcm: bytes) -> None:
        try:
            self._segments.put_nowait(pcm)
        except queue.Full:  # still busy with earlier speech; drop the oldest moment
            pass

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                pcm = self._segments.get(timeout=0.5)
            except queue.Empty:
                self._expire()
                continue
            try:
                result = self.engine.transcribe(write_wav(pcm, RATE))
            except (VoiceNotReady, VoiceNotInstalled) as e:
                log.info("speech skipped: %s", e)
                continue
            except Exception:
                log.exception("transcription failed")
                continue
            self.handle(result)

    def _expire(self) -> None:
        if self.mode == "command" and self.clock() > self.until:
            self.mode = "wake"

    # ---- what was said ------------------------------------------------------------------
    def handle(self, result: dict) -> None:
        """One understood utterance (VoiceEngine.transcribe's result)."""
        if result.get("noise") or not result.get("text"):
            return
        self._expire()
        if self.mode == "approval" and self.pending:
            if self.clock() > self.until:
                self.mode = "wake"
            elif result.get("reply"):
                self.heard = result["text"]
                self._decide(result["reply"] == "approve")
                return
        if self.mode == "command":
            self.heard = result["text"]
            self.mode = "wake"
            self._submit(result["text"].strip())
            return
        if result.get("wake"):
            self.heard = result["text"]
            command = (result.get("command") or "").strip()
            if len(command) >= 2:
                self._submit(command)
            else:
                self.mode, self.until = "command", self.clock() + COMMAND_SECONDS
                self.say("نعم؟")

    def _submit(self, command: str) -> None:
        try:
            task_id = self.orchestrator.submit(command, source="voice")
        except ValueError as e:
            self.say(str(e))
            return
        threading.Thread(target=self._follow, args=(task_id,), name="yousef-listener-task", daemon=True).start()

    def _follow(self, task_id: int) -> None:
        """Speak the outcome of a voice task, and ask for approval when it waits."""
        asked: set[int] = set()
        started = self.clock()
        while not self._stop.is_set() and self.clock() - started < TASK_TIMEOUT:
            task = self.memory.get_task(task_id)
            if task is None:
                return
            if task["status"] in DONE:
                if task["status"] == "done":
                    self.say(speakable(task.get("final") or "") or "تم.")
                elif task["status"] == "failed":
                    self.say("تعثّرت المهمة. " + speakable(task.get("error") or "", 160))
                return
            waiting = next((s for s in task["steps"] if s["status"] == "awaiting_approval"), None)
            if waiting and waiting["id"] not in asked:
                asked.add(waiting["id"])
                count = sum(1 for a in waiting.get("actions") or [] if a["status"] == "pending")
                count += sum(1 for c in waiting.get("changes") or [] if c["status"] == "pending")
                what = f"{count} اقتراحات" if count > 2 else ("اقتراحين" if count == 2 else "اقتراحًا")
                opening = speakable(waiting.get("output") or "", 200)
                self.pending = {"task_id": task_id, "step_id": waiting["id"]}
                self.mode, self.until = "approval", self.clock() + APPROVAL_SECONDS
                self.say(f"{opening} عندي {what} ينتظر موافقتك. قل: وافق، أو ارفض.".strip())
            time.sleep(0.5)

    def _decide(self, approve: bool) -> None:
        pending, self.pending, self.mode = self.pending, None, "wake"
        if pending and self.orchestrator.decide(pending["step_id"], approve):
            self.say("حسنًا، أنفّذ." if approve else "حسنًا، لن أنفّذ شيئًا.")

    def on_alert(self, alert: dict) -> None:
        """A price alert fired: say it when listening."""
        if self.running:
            from ..trading.paper import describe_alert
            threading.Thread(target=self.say, args=(f"تنبيه: {describe_alert(alert)}",), daemon=True).start()

    def say(self, text: str) -> None:
        if not text or not self.settings.get()["voice"].get("speak", True):
            return
        with self._say_lock:
            self._speaking.set()
            try:
                self.speaker.speak(text)
            finally:
                time.sleep(0.3)  # the room's echo
                self._speaking.clear()
