"""Microphone capture types and interface for Lecture Buddy (Person B).

The concrete microphone implementation lives behind ``AudioSource``.
``LiveRecordingManager`` uses only this interface.
"""

from __future__ import annotations

import math
import queue
import sys
import threading
import time
from array import array
from dataclasses import dataclass
from typing import Optional, Protocol

import numpy as np

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


CHUNK_SAMPLES = SAMPLE_RATE // 10  # ~100 ms per read()


def list_input_devices() -> list[tuple[str, str]]:
    """Return ``(device_id, name)`` pairs for every input-capable device."""
    import sounddevice as sd

    return [
        (str(index), info["name"])
        for index, info in enumerate(sd.query_devices())
        if info["max_input_channels"] > 0
    ]


class _StreamingResampler:
    """Low-pass + linear-interpolation resampler that keeps state across chunks."""

    def __init__(self, in_rate: float, out_rate: float, taps: int = 63) -> None:
        self._step = in_rate / out_rate
        self._pos = 0.0  # next output position, relative to self._prev
        self._prev: Optional[np.ndarray] = None

        # Windowed-sinc anti-aliasing filter, only needed when downsampling.
        if in_rate > out_rate:
            cutoff = 0.45 * out_rate / in_rate  # fraction of input sample rate
            n = np.arange(taps) - (taps - 1) / 2
            kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
            self._kernel = (kernel / kernel.sum()).astype(np.float32)
            self._history = np.zeros(taps - 1, dtype=np.float32)
        else:
            self._kernel = None
            self._history = None

    def process(self, x: np.ndarray) -> np.ndarray:
        if self._kernel is not None:
            padded = np.concatenate((self._history, x))
            self._history = padded[-(len(self._kernel) - 1):]
            x = np.convolve(padded, self._kernel, mode="valid")

        if self._prev is not None:
            x = np.concatenate((self._prev, x))
        n = len(x)
        if n < 2:
            self._prev = x
            return np.empty(0, dtype=np.float32)

        last = n - 1
        count = max(0, int(np.ceil((last - self._pos) / self._step)))
        positions = self._pos + self._step * np.arange(count)
        out = np.interp(positions, np.arange(n), x).astype(np.float32)

        self._pos = self._pos + self._step * count - last
        self._prev = x[-1:]
        return out


class AudioSource(Protocol):
    """Interface ``LiveRecordingManager`` uses to pull microphone audio.

    ``read`` returns PCM16 LE mono at ``SAMPLE_RATE`` (``b""`` on timeout).
    """

    def open(self, device_id: Optional[str]) -> None: ...

    def read(self, timeout: float) -> bytes: ...

    def close(self) -> None: ...


class SoundDeviceAudioSource:
    """``AudioSource`` backed by the ``sounddevice`` (PortAudio) library.

    It asks PortAudio for 16 kHz mono directly. If the device refuses, it
    captures at the device's native rate and channel count and downmixes and
    resamples here. The PortAudio callback only copies data into a queue; the
    conversion work happens in ``read()`` on the capture thread.
    """

    def __init__(self, chunk_samples: int = CHUNK_SAMPLES) -> None:
        self._chunk_samples = chunk_samples
        self._stream = None
        self._raw: "queue.Queue" = queue.Queue()
        self._pending = None  # converted float32 samples not yet returned
        self._resampler: Optional[_StreamingResampler] = None
        self._stream_error: Optional[str] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API

    def open(self, device_id: Optional[str]) -> None:
        import sounddevice as sd

        self.close()
        device = self._parse_device(device_id)
        self._raw = queue.Queue()
        self._pending = np.empty(0, dtype=np.float32)
        self._stream_error = None

        try:
            rate, channels = self._choose_format(sd, device)
            self._resampler = (
                None if rate == SAMPLE_RATE else _StreamingResampler(rate, SAMPLE_RATE)
            )
            stream = sd.InputStream(
                device=device,
                samplerate=rate,
                channels=channels,
                dtype="float32",
                blocksize=0,  # let PortAudio pick the most efficient size
                callback=self._callback,
                finished_callback=self._on_finished,
            )
            stream.start()
        except sd.PortAudioError as exc:
            message = str(exc)
            if "permission" in message.lower() or "access" in message.lower():
                raise PermissionError(message) from exc
            raise OSError(message) from exc
        except ValueError as exc:  # unknown / ambiguous device name
            raise OSError(str(exc)) from exc

        with self._lock:
            self._stream = stream

    def read(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while len(self._pending) < self._chunk_samples:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                block = self._raw.get(timeout=remaining)
            except queue.Empty:
                break
            self._append(block)
            # Drain anything else already waiting without blocking.
            while True:
                try:
                    self._append(self._raw.get_nowait())
                except queue.Empty:
                    break

        if len(self._pending) == 0:
            if self._stream_error is not None:
                raise OSError(self._stream_error)
            return b""

        out = self._pending[: self._chunk_samples]
        self._pending = self._pending[self._chunk_samples:]
        pcm = (np.clip(out, -1.0, 1.0) * 32767.0).astype("<i2")
        return pcm.tobytes()

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _parse_device(device_id: Optional[str]):
        if device_id is None or device_id == "":
            return None
        return int(device_id) if device_id.isdigit() else device_id

    @staticmethod
    def _choose_format(sd, device) -> tuple[float, int]:
        """Prefer 16 kHz mono; fall back to the device's native format."""
        try:
            sd.check_input_settings(
                device=device, samplerate=SAMPLE_RATE, channels=1, dtype="float32"
            )
            return SAMPLE_RATE, 1
        except sd.PortAudioError:
            pass

        info = sd.query_devices(device, "input")
        rate = float(info["default_samplerate"])
        for channels in (1, min(2, info["max_input_channels"]), info["max_input_channels"]):
            try:
                sd.check_input_settings(
                    device=device, samplerate=rate, channels=channels, dtype="float32"
                )
                return rate, channels
            except sd.PortAudioError:
                continue
        raise sd.PortAudioError(f"No usable input format for device {info['name']!r}")

    def _callback(self, indata, frames, time_info, status) -> None:
        # Runs on PortAudio's thread: keep it minimal. Overflows are dropped
        # silently; the sample timeline is driven by what we actually receive.
        self._raw.put(indata.mean(axis=1) if indata.shape[1] > 1 else indata[:, 0].copy())

    def _on_finished(self) -> None:
        # Called when the stream ends, including when the device disappears.
        if self._stream is not None:
            self._stream_error = "Audio input stream ended unexpectedly"

    def _append(self, block) -> None:
        if self._resampler is not None:
            block = self._resampler.process(block)
        if len(block):
            self._pending = np.concatenate((self._pending, block))


def rms_level(pcm: bytes) -> float:
    """Return the RMS level of PCM16 LE audio, scaled to 0.0-1.0."""
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    if not samples:
        return 0.0
    return min(1.0, math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768.0)
