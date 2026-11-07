"""Live recording session management for Lecture Buddy (Person B).

``LiveRecordingManager`` owns one live lecture session. It runs two worker
threads:

* **capture thread**: pulls PCM chunks from an ``AudioSource``, stamps them
  with a sequence number and sample offset (the authoritative timeline), and
  pushes them onto an in-memory queue. It never blocks on the network or disk,
  so recording continues even when transcription falls behind or drops out.
* **sender thread**: drains the queue, persists audio packets, groups them
  into batches and hands each batch to a ``Transcriber`` (the Gemini adapter).
  It handles reconnects with backoff and records uncovered audio intervals
  as gaps.

The microphone, the Gemini client and storage are NOT implemented here. They
are described by the ``AudioSource`` (audio_recording.py), ``Transcriber``
(audio_transcription.py) and ``TranscriptStore`` (below) protocols, so each
one can be implemented (or mocked) on its own.

Events go out through Qt signals, which are safe to emit from worker threads.
PySide6 queues them onto the receiver's (GUI) thread automatically.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol

from PySide6.QtCore import QObject, Signal

from .audio_recording import (
    BYTES_PER_SAMPLE,
    SAMPLE_RATE,
    AudioPacket,
    AudioSource,
    rms_level,
)
from .audio_transcription import Transcriber, TranscriptResult


# --------------------------------------------------------------------------- #
# Session records and storage interface.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TranscriptSegment:
    id: str
    lecture_id: str
    sequence: int
    text: str
    audio_range: tuple[int, int]
    timing_quality: str  # "model" or "approximate"


class LectureStatus(str, Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    RECORDING = "recording"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class TranscriptStore(Protocol):
    """Local persistence (SQLite plus audio files). Implemented separately."""

    def save_audio_packet(self, packet: AudioPacket) -> None: ...

    def append_final_segment(self, segment: TranscriptSegment) -> None: ...

    def mark_gap(self, lecture_id: str, start_sample: int, end_sample: int) -> None: ...


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #

_STOP = object()  # sentinel placed on the packet queue when capture ends


class LiveRecordingManager(QObject):
    """Runs one live lecture: microphone → batches → Gemini → saved segments.

    This is a singleton: only one manager (and so at most one live lecture)
    exists at a time. Construct it once at startup, then fetch it anywhere
    with ``LiveRecordingManager.instance()``. Constructing a second one raises
    ``RuntimeError``; call ``release()`` first if you really need to replace it
    (e.g. in tests).
    """

    _instance: Optional["LiveRecordingManager"] = None
    _instance_lock = threading.Lock()

    # Every signal carries lecture_id first, as the event contract requires.
    status_changed = Signal(str, str)  # lecture_id, LectureStatus value
    audio_level = Signal(str, float)  # lecture_id, RMS level 0.0–1.0
    transcript_interim = Signal(str, str)  # lecture_id, text
    transcript_final = Signal(str, object)  # lecture_id, TranscriptSegment
    gap_recorded = Signal(str, int, int)  # lecture_id, start_sample, end_sample
    processing_lag = Signal(str, float)  # lecture_id, seconds behind the mic
    processing_error = Signal(str, str, str, bool)  # id, code, message, retryable

    def __init__(
        self,
        audio_source: AudioSource,
        transcriber: Transcriber,
        store: Optional[TranscriptStore] = None,
        *,
        batch_seconds: float = 1.0,
        finish_timeout: float = 10.0,
        max_backoff: float = 10.0,
        parent: Optional[QObject] = None,
    ) -> None:
        cls = type(self)
        with cls._instance_lock:
            if cls._instance is not None:
                raise RuntimeError(
                    "LiveRecordingManager already exists; use LiveRecordingManager.instance()"
                )
            cls._instance = self

        super().__init__(parent)
        self._source = audio_source
        self._transcriber = transcriber
        self._store = store
        self._batch_samples = max(1, int(batch_seconds * SAMPLE_RATE))
        self._finish_timeout = finish_timeout
        self._max_backoff = max_backoff

        self._lock = threading.Lock()
        self._status = LectureStatus.IDLE
        self._lecture_id = ""
        self._stop_event = threading.Event()
        self._packets: queue.Queue = queue.Queue()
        self._capture_thread: Optional[threading.Thread] = None
        self._sender_thread: Optional[threading.Thread] = None

        # Timeline state.
        self._captured_end_sample = 0  # written by capture thread only
        self._sent_end_sample = 0  # written by sender thread only
        self._last_final_end_sample = 0
        self._segment_sequence = 0

        # Connection and gap state (sender thread only).
        self._connected = False
        self._backoff = 0.5
        self._next_connect_at = 0.0
        self._gap_start: Optional[int] = None
        self._gap_end = 0

    # ------------------------------------------------------------ singleton

    @classmethod
    def instance(cls) -> "LiveRecordingManager":
        """Return the single manager. Raises if it has not been created yet."""
        inst = cls._instance
        if inst is None:
            raise RuntimeError("LiveRecordingManager has not been created yet")
        return inst

    @classmethod
    def has_instance(cls) -> bool:
        return cls._instance is not None

    @classmethod
    def release(cls) -> None:
        """Forget the current manager so a new one can be constructed.

        Refuses while a lecture is still active; stop it first.
        """
        with cls._instance_lock:
            inst = cls._instance
            if inst is not None and inst.is_active():
                raise RuntimeError("Cannot release the manager while a lecture is active")
            cls._instance = None

    # ------------------------------------------------------------------ API

    @property
    def status(self) -> LectureStatus:
        return self._status

    @property
    def lecture_id(self) -> str:
        return self._lecture_id

    def is_active(self) -> bool:
        return self._status in (
            LectureStatus.CONNECTING,
            LectureStatus.RECORDING,
            LectureStatus.RECONNECTING,
            LectureStatus.STOPPING,
        )

    def start(self, lecture_id: str, device_id: Optional[str] = None) -> None:
        """Open the microphone and start both worker threads.

        Returns immediately. Progress is reported through ``status_changed``.
        A microphone failure is reported via ``processing_error`` and leaves
        the manager in ``ERROR`` so the UI can offer a retry.
        """
        # Check and claim the session atomically so two concurrent start()
        # calls cannot both begin recording.
        with self._lock:
            if self.is_active():
                raise RuntimeError(
                    f"Lecture {self._lecture_id!r} is already being recorded"
                )
            self._status = LectureStatus.CONNECTING

        self._reset(lecture_id)
        self.status_changed.emit(lecture_id, LectureStatus.CONNECTING.value)

        try:
            self._source.open(device_id)
        except PermissionError as exc:
            self._fail("mic_permission_denied", f"Microphone permission denied: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - device errors vary by backend
            self._fail("mic_unavailable", f"Could not open microphone: {exc}")
            return

        self._capture_thread = threading.Thread(
            target=self._capture_loop, name="lecture-capture", daemon=True
        )
        self._sender_thread = threading.Thread(
            target=self._send_loop, name="lecture-sender", daemon=True
        )
        self._capture_thread.start()
        self._sender_thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        """Stop capture, flush buffered audio, wait for final text, then close.

        Blocks until shutdown is done or ``timeout`` passes, and keeps whatever
        was finalized up to that point. Call ``stop_async`` from the GUI thread
        if you do not want it to block.
        """
        if not self.is_active():
            return
        self._set_status(LectureStatus.STOPPING)
        self._stop_event.set()

        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in (self._capture_thread, self._sender_thread):
            if thread is None:
                continue
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            thread.join(remaining)

        if self._sender_thread and self._sender_thread.is_alive():
            self._emit_error("stop_timeout", "Stopping timed out; partial transcript kept", False)
        self._set_status(LectureStatus.STOPPED)

    def stop_async(self) -> None:
        """Non-blocking ``stop``. Watch ``status_changed`` for ``stopped``."""
        threading.Thread(target=self.stop, name="lecture-stop", daemon=True).start()

    # -------------------------------------------------------- capture thread

    def _capture_loop(self) -> None:
        sequence = 0
        try:
            while not self._stop_event.is_set():
                pcm = self._source.read(timeout=0.2)
                if not pcm:
                    continue
                if len(pcm) % BYTES_PER_SAMPLE:
                    pcm = pcm[: -(len(pcm) % BYTES_PER_SAMPLE)]
                sample_count = len(pcm) // BYTES_PER_SAMPLE

                packet = AudioPacket(
                    lecture_id=self._lecture_id,
                    sequence=sequence,
                    start_sample=self._captured_end_sample,
                    sample_count=sample_count,
                    pcm_bytes=pcm,
                )
                sequence += 1
                self._captured_end_sample = packet.end_sample
                self._packets.put(packet)
                self.audio_level.emit(self._lecture_id, rms_level(pcm))
        except Exception as exc:  # noqa: BLE001
            self._emit_error("mic_failed", f"Microphone capture stopped: {exc}", True)
            self._stop_event.set()
        finally:
            try:
                self._source.close()
            except Exception:  # noqa: BLE001
                pass
            # Tell the sender that no more audio is coming.
            self._packets.put(_STOP)

    # --------------------------------------------------------- sender thread

    def _send_loop(self) -> None:
        self._try_connect()

        batch: list[AudioPacket] = []
        batch_samples = 0
        draining = False

        while True:
            try:
                item = self._packets.get(timeout=0.1)
            except queue.Empty:
                item = None

            if item is _STOP:
                draining = True
            elif item is not None:
                packet: AudioPacket = item
                self._save_audio(packet)
                batch.append(packet)
                batch_samples += packet.sample_count

            # Send once the batch is full, or flush whatever is left at the end.
            if batch and (batch_samples >= self._batch_samples or draining):
                self._dispatch(batch)
                batch, batch_samples = [], 0

            if draining and self._packets.empty():
                break

        self._finish_stream()

    def _dispatch(self, batch: list[AudioPacket]) -> None:
        if not self._connected and time.monotonic() >= self._next_connect_at:
            self._try_connect()

        if self._connected:
            try:
                self._transcriber.send_batch(batch)
                self._sent_end_sample = batch[-1].end_sample
            except Exception as exc:  # noqa: BLE001
                self._on_connection_lost(exc)

        if not self._connected:
            # Nothing reached Gemini for this audio: extend the open gap. The
            # audio is still saved locally, so it can be backfilled later.
            if self._gap_start is None:
                self._gap_start = batch[0].start_sample
            self._gap_end = batch[-1].end_sample
            self._sent_end_sample = batch[-1].end_sample

        lag = (self._captured_end_sample - self._sent_end_sample) / SAMPLE_RATE
        self.processing_lag.emit(self._lecture_id, max(0.0, lag))

    def _try_connect(self) -> None:
        try:
            self._transcriber.connect(self._on_result, self._on_async_error)
        except Exception as exc:  # noqa: BLE001
            self._connected = False
            self._schedule_reconnect()
            self._emit_error("transcriber_connect_failed", f"Transcription unavailable: {exc}", True)
            self._set_status(LectureStatus.RECONNECTING)
            return

        self._connected = True
        self._backoff = 0.5
        self._close_gap()
        if not self._stop_event.is_set():
            self._set_status(LectureStatus.RECORDING)

    def _on_connection_lost(self, exc: Exception) -> None:
        self._connected = False
        try:
            self._transcriber.close()
        except Exception:  # noqa: BLE001
            pass
        self._schedule_reconnect()
        self._emit_error("transcriber_disconnected", f"Transcription connection lost: {exc}", True)
        if not self._stop_event.is_set():
            self._set_status(LectureStatus.RECONNECTING)

    def _schedule_reconnect(self) -> None:
        self._next_connect_at = time.monotonic() + self._backoff
        self._backoff = min(self._backoff * 2, self._max_backoff)

    def _close_gap(self) -> None:
        if self._gap_start is None:
            return
        start, end = self._gap_start, self._gap_end
        self._gap_start = None
        if self._store is not None:
            try:
                self._store.mark_gap(self._lecture_id, start, end)
            except Exception as exc:  # noqa: BLE001
                self._emit_error("storage_failed", f"Could not record gap: {exc}", False)
        self.gap_recorded.emit(self._lecture_id, start, end)

    def _finish_stream(self) -> None:
        if self._connected:
            try:
                self._transcriber.finish(self._finish_timeout)
            except Exception as exc:  # noqa: BLE001
                self._emit_error("finish_failed", f"Final transcript may be incomplete: {exc}", False)
        try:
            self._transcriber.close()
        except Exception:  # noqa: BLE001
            pass
        self._connected = False
        self._close_gap()  # any interval never transcribed stays a visible gap

    def _save_audio(self, packet: AudioPacket) -> None:
        if self._store is None:
            return
        try:
            self._store.save_audio_packet(packet)
        except Exception as exc:  # noqa: BLE001
            self._emit_error("audio_save_failed", f"Could not save audio: {exc}", False)

    # ------------------------------------------- transcriber callbacks (any thread)

    def _on_result(self, result: TranscriptResult) -> None:
        if not result.is_final:
            self.transcript_interim.emit(self._lecture_id, result.text)
            return

        with self._lock:
            if result.audio_range is not None:
                audio_range, quality = result.audio_range, "model"
            else:
                # The arrival time of a response does not tell us exact word
                # timing. Approximate with the audio sent since the last final.
                audio_range = (self._last_final_end_sample, self._sent_end_sample)
                quality = "approximate"
            self._last_final_end_sample = max(self._last_final_end_sample, audio_range[1])

            segment = TranscriptSegment(
                id=str(uuid.uuid4()),
                lecture_id=self._lecture_id,
                sequence=self._segment_sequence,
                text=result.text,
                audio_range=audio_range,
                timing_quality=quality,
            )
            self._segment_sequence += 1

        # Persist before notifying the UI.
        if self._store is not None:
            try:
                self._store.append_final_segment(segment)
            except Exception as exc:  # noqa: BLE001
                self._emit_error("segment_save_failed", f"Could not save transcript: {exc}", True)
        self.transcript_final.emit(self._lecture_id, segment)

    def _on_async_error(self, exc: Exception) -> None:
        # The sender thread owns the connection state. Mark it disconnected so
        # the next batch triggers a reconnect and its audio becomes a gap.
        self._connected = False
        self._schedule_reconnect()
        self._emit_error("transcriber_error", f"Transcription error: {exc}", True)
        if not self._stop_event.is_set():
            self._set_status(LectureStatus.RECONNECTING)

    # --------------------------------------------------------------- helpers

    def _reset(self, lecture_id: str) -> None:
        self._lecture_id = lecture_id
        self._stop_event.clear()
        self._packets = queue.Queue()
        self._captured_end_sample = 0
        self._sent_end_sample = 0
        self._last_final_end_sample = 0
        self._segment_sequence = 0
        self._connected = False
        self._backoff = 0.5
        self._next_connect_at = 0.0
        self._gap_start = None
        self._gap_end = 0

    def _set_status(self, status: LectureStatus) -> None:
        with self._lock:
            if self._status == status:
                return
            self._status = status
        self.status_changed.emit(self._lecture_id, status.value)

    def _fail(self, code: str, message: str) -> None:
        self._emit_error(code, message, True)
        self._set_status(LectureStatus.ERROR)

    def _emit_error(self, code: str, message: str, retryable: bool) -> None:
        self.processing_error.emit(self._lecture_id, code, message, retryable)
