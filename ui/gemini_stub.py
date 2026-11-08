# gemini_stub.py
#
# Service facade used by the UI. Recording and live transcription go through
# the real LiveRecordingManager (live_recording/); the other methods are
# still mocked.

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Slot

# The UI runs as ``python ui/main.py``, so the repo root is not on sys.path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from live_recording.audio_recording import SAMPLE_RATE, SoundDeviceAudioSource  # noqa: E402
from live_recording.audio_transcription import GeminiTranscriber  # noqa: E402
from live_recording.audio_transcription import DEFAULT_MODEL  # noqa: E402
from live_recording.live_recording import (  # noqa: E402
    LectureStatus,
    LiveRecordingManager,
    TranscriptSegment,
)


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader so GEMINI_KEY can live in the repo root .env."""
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip().removeprefix("export ").strip()
                os.environ.setdefault(key, value.strip().strip("'\""))
    except FileNotFoundError:
        pass


class GeminiServiceStub(QObject):
    """UI-facing service. All signals are delivered on the GUI thread."""

    # Interim text for the utterance in progress (replaces the previous one).
    transcript_interim = Signal(str)
    # Finalized text plus its start time in seconds since the first recording.
    transcript_final = Signal(str, float)
    # LectureStatus value: connecting, recording, reconnecting, stopping, stopped, error.
    recording_status = Signal(str)
    # Human-readable error message.
    recording_error = Signal(str)
    transcript_segment = Signal(object, float)  # original stable record, session offset in seconds
    recording_finished = Signal(bool)  # False if transcription failed or timed out
    gap_recorded = Signal(int, int)  # sample offsets across recording sessions

    def __init__(self, device_id: Optional[str] = None, *, source_factory=None, transcriber_factory=None):
        super().__init__()
        _load_dotenv(_REPO_ROOT / ".env")
        self.api_key = os.environ.get("GEMINI_KEY", "")
        self.device_id = device_id or os.environ.get("AUDIO_INPUT_DEVICE") or None
        self._source_factory = source_factory or SoundDeviceAudioSource
        self._transcriber_factory = transcriber_factory
        self._had_errors = False
        self._manager: Optional[LiveRecordingManager] = None
        self._lecture_id = ""
        # Each start() restarts the manager's sample timeline at 0, so keep
        # an offset to give a continuous timeline across stop/start.
        self._offset_seconds = 0.0
        self._session_start = 0.0  # offset of the current/most recent session
        self._started_at: Optional[float] = None

    # ------------------------------------------------------------ recording

    def start_recording(self) -> bool:
        """Open the mic and begin streaming to Gemini. Returns False on failure."""
        self._had_errors = False
        manager = self._ensure_manager()
        if manager is None:
            return False
        if manager.is_active():
            self.recording_error.emit("Previous recording is still finishing; try again shortly.")
            return False

        self._lecture_id = f"lecture-{uuid.uuid4()}"
        self._session_start = self._offset_seconds
        self._started_at = time.monotonic()
        manager.start(self._lecture_id, self.device_id)
        if manager.status == LectureStatus.ERROR:
            self._started_at = None
            return False
        print("[Gemini] Recording started.")
        return True

    def stop_recording(self) -> None:
        """Stop capture without blocking; final text keeps arriving until 'stopped'."""
        if self._started_at is not None:
            self._offset_seconds += time.monotonic() - self._started_at
            self._started_at = None
        if self._manager is not None and self._manager.is_active():
            self._manager.stop_async()
            print("[Gemini] Recording stopping.")

    def shutdown(self) -> None:
        """Blocking stop for application exit."""
        if self._manager is not None and self._manager.is_active():
            self._manager.stop(timeout=5.0)
        if self._manager is not None and not self._manager.is_active():
            if LiveRecordingManager.has_instance() and LiveRecordingManager.instance() is self._manager:
                LiveRecordingManager.release()
            self._manager = None

    def _ensure_manager(self) -> Optional[LiveRecordingManager]:
        if self._manager is not None:
            return self._manager
        try:
            transcriber = (self._transcriber_factory() if self._transcriber_factory else
                GeminiTranscriber(api_key=self.api_key or None,
                    model=os.getenv("GEMINI_TRANSCRIPTION_MODEL") or DEFAULT_MODEL))
            manager = LiveRecordingManager(self._source_factory(), transcriber, parent=self)
        except Exception as exc:  # noqa: BLE001 - missing key / SDK problems
            self.recording_error.emit(f"Transcription unavailable: {exc}")
            return None

        # Manager signals are emitted from worker threads; connecting them to
        # slots on this GUI-thread object makes Qt queue them onto the GUI.
        manager.status_changed.connect(self._on_status)
        manager.transcript_interim.connect(self._on_interim)
        manager.transcript_final.connect(self._on_final)
        manager.processing_error.connect(self._on_error)
        manager.gap_recorded.connect(self._on_gap)
        self._manager = manager
        return manager

    # Each record session gets its own lecture_id; drop late events from an
    # older session that may still be draining.

    @Slot(str, str)
    def _on_status(self, lecture_id: str, status: str) -> None:
        if lecture_id == self._lecture_id:
            self.recording_status.emit(status)
            if status == "stopped":
                self.recording_finished.emit(not self._had_errors)

    @Slot(str, str)
    def _on_interim(self, lecture_id: str, text: str) -> None:
        if lecture_id == self._lecture_id:
            self.transcript_interim.emit(text)

    @Slot(str, object)
    def _on_final(self, lecture_id: str, segment: TranscriptSegment) -> None:
        if lecture_id != self._lecture_id:
            return
        start = self._session_start + segment.audio_range[0] / SAMPLE_RATE
        self.transcript_segment.emit(segment, self._session_start)
        self.transcript_final.emit(segment.text, start)

    @Slot(str, int, int)
    def _on_gap(self, lecture_id, start, end):
        if lecture_id == self._lecture_id:
            self._had_errors = True
            offset = int(self._session_start * SAMPLE_RATE)
            self.gap_recorded.emit(start + offset, end + offset)

    @Slot(str, str, str, bool)
    def _on_error(self, lecture_id: str, code: str, message: str, _retryable: bool) -> None:
        if lecture_id == self._lecture_id:
            self._had_errors = True
            print(f"[Gemini] {code}: {message}")
            self.recording_error.emit(message)

    # ---------------------------------------------------------------- mocks

    def import_slides(self, path):
        print(f"[GeminiStub] Slides imported: {path}")

    def request_im_lost(self, slide):
        print(f"[GeminiStub] 'I'm Lost' requested on slide {slide}")

    def export_pdf(self, path):
        print(f"[GeminiStub] Exported PDF to {path}")
