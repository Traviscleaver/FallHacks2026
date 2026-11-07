"""Microphone capture types and interface for Lecture Buddy (Person B).

The concrete microphone implementation lives behind ``AudioSource``.
``LiveRecordingManager`` uses only this interface.
"""

from __future__ import annotations

import math
import sys
from array import array
from dataclasses import dataclass
from typing import Optional, Protocol

# Audio format agreed for the whole pipeline: 16 kHz, mono, signed 16-bit LE.
SAMPLE_RATE = 16_000
BYTES_PER_SAMPLE = 2


@dataclass(frozen=True)
class AudioPacket:
    lecture_id: str
    sequence: int
    start_sample: int
    sample_count: int
    pcm_bytes: bytes

    @property
    def end_sample(self) -> int:
        return self.start_sample + self.sample_count


class AudioSource(Protocol):
    """Microphone capture. Implemented separately (e.g. with sounddevice).

    It must deliver 16 kHz mono PCM16 little-endian audio, already downmixed
    and resampled.
    """

    def open(self, device_id: Optional[str]) -> None:
        """Start capturing. Raise ``PermissionError`` or ``OSError`` on failure."""

    def read(self, timeout: float) -> bytes:
        """Block until about 100 ms of PCM is available or ``timeout`` passes.

        Return ``b""`` if no audio arrived before the timeout.
        """

    def close(self) -> None:
        """Stop capturing and release the device."""


def rms_level(pcm: bytes) -> float:
    """Return the RMS level of PCM16 LE audio, scaled to 0.0-1.0."""
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    return min(1.0, math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0)
