"""Read the WAV clips the dashboard records (16-bit PCM), using only the standard library."""

from __future__ import annotations

import io
import wave
from array import array
from dataclasses import dataclass

MAX_SECONDS = 60
MIN_SECONDS = 0.2


class AudioError(ValueError):
    pass


@dataclass
class Clip:
    pcm: bytes  # mono, 16-bit little-endian
    rate: int

    @property
    def seconds(self) -> float:
        return len(self.pcm) / 2 / self.rate


def read_wav(data: bytes) -> Clip:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if width != 2:
                raise AudioError("expected 16-bit PCM audio")
            if channels not in (1, 2):
                raise AudioError("expected mono or stereo audio")
            if not 8000 <= rate <= 48000:
                raise AudioError("unsupported sample rate")
            if frames / rate > MAX_SECONDS:
                raise AudioError(f"clip longer than {MAX_SECONDS} seconds")
            raw = w.readframes(frames)
    except (wave.Error, EOFError) as e:
        raise AudioError(f"not a valid WAV file: {e}") from None
    if channels == 2:
        samples = array("h")
        samples.frombytes(raw[: len(raw) - len(raw) % 4])
        mono = array("h", ((samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples) - 1, 2)))
        raw = mono.tobytes()
    clip = Clip(pcm=raw, rate=rate)
    if clip.seconds < MIN_SECONDS:
        raise AudioError("clip is too short")
    return clip


def write_wav(pcm: bytes, rate: int = 16000) -> bytes:
    """Used by tests and tools."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
