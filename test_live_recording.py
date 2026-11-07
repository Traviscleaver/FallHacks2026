"""Manual end-to-end test for LiveRecordingManager with Gemini transcription.

Uses the real microphone and the real ``GeminiTranscriber``; only the store is
a mock. This checks the full path:
mic -> capture thread -> packet queue -> sender thread -> Gemini Live -> transcript.

Interim text is shown live next to the mic meter, and final segments are
printed as they arrive. When the run ends it prints the full transcript, a
short report, and saves the captured audio to a WAV file so you can compare
what was said with what Gemini heard.

Needs ``GEMINI_KEY`` in the environment or in a ``.env`` file in the repo root.

Usage (from the repo root):
    python test_live_recording.py                      # 20 s on the default mic
    python test_live_recording.py -s 60 -d 9           # 60 s on device 9
    python test_live_recording.py -l en-US --mode SMART -v "eigenvalue" -v "Fourier"
    python test_live_recording.py --list-devices
Press Ctrl+C to stop early. Speak during the run, or the transcript checks fail.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
import wave
from typing import Callable, Optional, Sequence

from PySide6.QtCore import QCoreApplication, QObject, QTimer, Slot

from live_recording.audio_recording import (
    BYTES_PER_SAMPLE,
    SAMPLE_RATE,
    AudioPacket,
    SoundDeviceAudioSource,
    list_input_devices,
)
from live_recording.audio_transcription import (
    DEFAULT_MODEL,
    GeminiTranscriber,
    TranscriptResult,
)
from live_recording.live_recording import LiveRecordingManager, TranscriptSegment


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env reader so the test needs no extra dependency."""
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().removeprefix("export ").strip()
                value = value.strip().strip("'\"")
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- #
# Instrumented transcriber + mock store
# --------------------------------------------------------------------------- #


class CountingTranscriber:
    """Wraps a real ``Transcriber`` and records what passes through it."""

    def __init__(self, inner: GeminiTranscriber) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.connect_calls = 0
        self.close_calls = 0
        self.batches = 0
        self.samples = 0
        self.interim_results = 0
        self.final_results = 0
        self.first_send_at: Optional[float] = None
        self.first_result_at: Optional[float] = None
        self.send_times: list[float] = []

    def connect(
        self,
        on_result: Callable[[TranscriptResult], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        with self._lock:
            self.connect_calls += 1

        def counted(result: TranscriptResult) -> None:
            with self._lock:
                if self.first_result_at is None:
                    self.first_result_at = time.monotonic()
                if result.is_final:
                    self.final_results += 1
                else:
                    self.interim_results += 1
            on_result(result)

        self._inner.connect(counted, on_error)

    def send_batch(self, packets: Sequence[AudioPacket]) -> None:
        start = time.monotonic()
        self._inner.send_batch(packets)
        with self._lock:
            self.send_times.append(time.monotonic() - start)
            if self.first_send_at is None:
                self.first_send_at = start
            self.batches += 1
            self.samples += sum(p.sample_count for p in packets)

    def finish(self, timeout: float) -> None:
        self._inner.finish(timeout)

    def close(self) -> None:
        with self._lock:
            self.close_calls += 1
        self._inner.close()


class MockStore:
    """Stands in for SQLite/audio files. Keeps everything in memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.packets: list[AudioPacket] = []
        self.segments: list[TranscriptSegment] = []
        self.gaps: list[tuple[str, int, int]] = []

    def save_audio_packet(self, packet: AudioPacket) -> None:
        with self._lock:
            self.packets.append(packet)

    def append_final_segment(self, segment: TranscriptSegment) -> None:
        with self._lock:
            self.segments.append(segment)

    def mark_gap(self, lecture_id: str, start_sample: int, end_sample: int) -> None:
        with self._lock:
            self.gaps.append((lecture_id, start_sample, end_sample))

    def write_wav(self, path: str) -> None:
        with self._lock, wave.open(path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(BYTES_PER_SAMPLE)
            wav.setframerate(SAMPLE_RATE)
            for packet in self.packets:
                wav.writeframes(packet.pcm_bytes)


# --------------------------------------------------------------------------- #
# Signal listener (lives on the main thread, so slots run there)
# --------------------------------------------------------------------------- #


class Listener(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.statuses: list[str] = []
        self.errors: list[tuple[str, str, bool]] = []
        self.peak_level = 0.0
        self.max_lag = 0.0
        self.interim_count = 0
        self.last_interim = ""

    @Slot(str, str)
    def on_status(self, lecture_id: str, status: str) -> None:
        self.statuses.append(status)
        self._line(f"status -> {status}")

    @Slot(str, float)
    def on_level(self, lecture_id: str, level: float) -> None:
        self.peak_level = max(self.peak_level, level)
        bar = "#" * int(min(1.0, level * 8) * 20)  # x8 so normal speech is visible
        tail = self.last_interim[-50:]
        sys.stdout.write(f"\r  mic {level:5.3f} |{bar:<20}| {tail:<50}")
        sys.stdout.flush()

    @Slot(str, str)
    def on_interim(self, lecture_id: str, text: str) -> None:
        self.interim_count += 1
        self.last_interim = " ".join(text.split())

    @Slot(str, object)
    def on_final(self, lecture_id: str, segment: TranscriptSegment) -> None:
        self.last_interim = ""
        start, end = segment.audio_range
        self._line(
            f"final #{segment.sequence} "
            f"[{start / SAMPLE_RATE:6.2f}s-{end / SAMPLE_RATE:6.2f}s, {segment.timing_quality}]: "
            f"{segment.text}"
        )

    @Slot(str, int, int)
    def on_gap(self, lecture_id: str, start: int, end: int) -> None:
        self._line(f"gap: {start / SAMPLE_RATE:.2f}s-{end / SAMPLE_RATE:.2f}s")

    @Slot(str, float)
    def on_lag(self, lecture_id: str, seconds: float) -> None:
        self.max_lag = max(self.max_lag, seconds)

    @Slot(str, str, str, bool)
    def on_error(self, lecture_id: str, code: str, message: str, retryable: bool) -> None:
        self.errors.append((code, message, retryable))
        self._line(f"ERROR {code}: {message} (retryable={retryable})")

    @staticmethod
    def _line(text: str) -> None:
        sys.stdout.write("\r" + " " * 90 + "\r" + text + "\n")
        sys.stdout.flush()


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def report(
    elapsed: float,
    store: MockStore,
    transcriber: CountingTranscriber,
    listener: Listener,
    wav_path: str,
) -> bool:
    packets = store.packets
    total_samples = sum(p.sample_count for p in packets)
    recorded = total_samples / SAMPLE_RATE
    gap_samples = sum(end - start for _, start, end in store.gaps)

    checks: list[tuple[str, bool, str]] = []

    # Capture path.
    checks.append(("packets captured", len(packets) > 0, f"{len(packets)} packets"))

    contiguous = all(
        a.end_sample == b.start_sample and b.sequence == a.sequence + 1
        for a, b in zip(packets, packets[1:])
    ) and (not packets or packets[0].start_sample == 0)
    checks.append(("timeline contiguous", contiguous, "no missing/overlapping packets"))

    ratio = recorded / elapsed if elapsed else 0.0
    checks.append(
        (
            "audio duration ~ wall clock",
            0.9 <= ratio <= 1.1,
            f"{recorded:.2f}s audio in {elapsed:.2f}s ({ratio:.0%})",
        )
    )

    checks.append(
        ("mic not silent", listener.peak_level > 0.001, f"peak RMS {listener.peak_level:.4f}")
    )

    # Gemini path.
    checks.append(
        (
            "connected to Gemini",
            transcriber.connect_calls >= 1 and "recording" in listener.statuses,
            f"{transcriber.connect_calls} connect call(s)",
        )
    )

    checks.append(
        (
            "all audio sent to Gemini",
            transcriber.samples + gap_samples == total_samples,
            f"{transcriber.samples} sent + {gap_samples} in gaps / {total_samples} samples",
        )
    )

    checks.append(
        (
            "interim transcripts received",
            listener.interim_count > 0,
            f"{transcriber.interim_results} from Gemini, {listener.interim_count} emitted",
        )
    )

    checks.append(
        (
            "final transcript received",
            len(store.segments) > 0,
            f"{transcriber.final_results} from Gemini, {len(store.segments)} stored",
        )
    )

    sequences = [s.sequence for s in store.segments]
    checks.append(
        (
            "segment sequence ordered",
            sequences == sorted(sequences) and len(set(sequences)) == len(sequences),
            ", ".join(map(str, sequences)) or "none",
        )
    )

    in_range = all(
        0 <= s.audio_range[0] <= s.audio_range[1] <= total_samples for s in store.segments
    )
    checks.append(("segment audio ranges valid", in_range, "within captured timeline"))

    checks.append(
        (
            "status sequence",
            listener.statuses[:1] == ["connecting"] and listener.statuses[-1:] == ["stopped"],
            " -> ".join(listener.statuses),
        )
    )

    checks.append(("no errors", not listener.errors, f"{len(listener.errors)} error(s)"))

    print("\n\n===== Transcript =====")
    if store.segments:
        for segment in store.segments:
            print(f"  {segment.text}")
    else:
        print("  (empty)")

    print("\n===== LiveRecordingManager + Gemini test report =====")
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    latency = (
        f"{transcriber.first_result_at - transcriber.first_send_at:.2f}s"
        if transcriber.first_send_at and transcriber.first_result_at
        else "n/a"
    )
    sends = transcriber.send_times
    send_info = (
        f"avg send {sum(sends) / len(sends) * 1000:.0f}ms, max {max(sends) * 1000:.0f}ms"
        if sends
        else "no sends"
    )
    print(
        f"  info: {transcriber.batches} batches ({send_info}), "
        f"first result after {latency}, {len(store.gaps)} gaps, "
        f"max lag {listener.max_lag:.2f}s"
    )
    print(f"  audio saved to {wav_path}; play it back and compare with the transcript")
    return all(ok for _, ok, _ in checks)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-s", "--seconds", type=float, default=20.0, help="recording length (default 20)")
    parser.add_argument("-d", "--device", default=None, help="input device id or name (default: system default)")
    parser.add_argument("-o", "--out", default="test_recording.wav", help="WAV output path")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Gemini model (default {DEFAULT_MODEL})")
    parser.add_argument("-l", "--language", action="append", default=[], help="language code, repeatable (e.g. en-US)")
    parser.add_argument("-v", "--vocab", action="append", default=[], help="custom vocabulary term, repeatable")
    parser.add_argument("--mode", choices=["VERBATIM", "SMART"], default=None, help="transcription mode")
    parser.add_argument("-b", "--batch-seconds", type=float, default=1.0, help="audio per send (default 1.0)")
    parser.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    args = parser.parse_args()

    if args.list_devices:
        for device_id, name in list_input_devices():
            print(f"{device_id:>4}  {name}")
        return 0

    load_dotenv()
    try:
        gemini = GeminiTranscriber(
            model=args.model,
            language_codes=args.language,
            custom_vocabulary=args.vocab,
            mode=args.mode,
        )
    except RuntimeError as exc:
        print(f"Cannot create GeminiTranscriber: {exc}")
        return 1

    app = QCoreApplication(sys.argv)

    store = MockStore()
    transcriber = CountingTranscriber(gemini)
    manager = LiveRecordingManager(
        SoundDeviceAudioSource(), transcriber, store, batch_seconds=args.batch_seconds
    )
    listener = Listener()

    manager.status_changed.connect(listener.on_status)
    manager.audio_level.connect(listener.on_level)
    manager.transcript_interim.connect(listener.on_interim)
    manager.transcript_final.connect(listener.on_final)
    manager.gap_recorded.connect(listener.on_gap)
    manager.processing_lag.connect(listener.on_lag)
    manager.processing_error.connect(listener.on_error)

    started_at = 0.0
    elapsed = 0.0

    def finish() -> None:
        nonlocal elapsed
        if not manager.is_active():
            app.quit()
            return
        elapsed = time.monotonic() - started_at
        Listener._line("stopping; waiting for Gemini to deliver the last finals...")
        manager.stop(timeout=15.0)
        # Let queued signals (final segments, "stopped") reach the listener.
        QTimer.singleShot(300, app.quit)

    # Ctrl+C: Python only runs signal handlers between bytecodes, so wake the
    # interpreter periodically while Qt's event loop is running.
    signal.signal(signal.SIGINT, lambda *_: QTimer.singleShot(0, finish))
    heartbeat = QTimer()
    heartbeat.start(100)
    heartbeat.timeout.connect(lambda: None)

    device_label = args.device if args.device is not None else "default"
    print(
        f"Recording {args.seconds:.0f}s from device '{device_label}' with {args.model}. "
        "Speak into the mic (Ctrl+C to stop)."
    )
    started_at = time.monotonic()
    manager.start("test-lecture", args.device)

    if manager.status.value == "error":
        # start() failed to open the mic; the error signal is already queued.
        QTimer.singleShot(100, app.quit)
    else:
        QTimer.singleShot(int(args.seconds * 1000), finish)

    app.exec()

    if not store.packets:
        print("\nNo audio was captured.")
        for code, message, _ in listener.errors:
            print(f"  {code}: {message}")
        LiveRecordingManager.release()
        return 1

    store.write_wav(args.out)
    ok = report(elapsed, store, transcriber, listener, args.out)
    LiveRecordingManager.release()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
