"""Qt integration. One finite executor worker, coalesced timer checks, queued signals."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from queue import Empty, SimpleQueue

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot

from .repository import StaleSnapshot
from .models import Segment
from .pdf_input import extract_pdf
from .slide_preparation import stable_id
from .gemini_adapter import safe_error_message
from .final_notes import FinalNotesService


class InterpretationController(QObject):
    slides_ready = Signal(str, object)
    slide_progress = Signal(str, int, int)
    section_updated = Signal(str, object)
    notes_updated = Signal(str, object)
    analysis_progress = Signal(str, object)
    processing_error = Signal(str, str, str, bool)
    final_sections_ready = Signal(str, object)
    pdf_loaded = Signal(str, object)
    state_updated = Signal(str, object)
    shutdown_complete = Signal()
    final_notes_ready = Signal(str, object, str)
    _completed = Signal(bool)

    def __init__(self, slide_service, notes_service, recording_manager=None, parent=None):
        super().__init__(parent)
        self.slides, self.notes = slide_service, notes_service
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="interpretation")
        self._events = SimpleQueue()
        self._lectures = set()  # worker-owned
        self._busy = False
        self._closed = False
        self._pending_events = deque()  # worker-owned retry queue
        self._recording = recording_manager
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, int(notes_service.settings.analysis_seconds * 1000)))
        self._timer.timeout.connect(self._tick)
        self._completed.connect(self._done, Qt.ConnectionType.QueuedConnection)
        if recording_manager is not None:
            recording_manager.transcript_final.connect(self.on_transcript_final, Qt.ConnectionType.QueuedConnection)
            recording_manager.gap_recorded.connect(self.on_gap, Qt.ConnectionType.QueuedConnection)
        self._timer.start()

    @Slot(str, object)
    def on_transcript_final(self, lecture_id, segment):
        # No model/repository call in a Qt slot or transcription callback.
        self._events.put(("segment", lecture_id, segment))

    @Slot(str, int, int)
    def on_gap(self, lecture_id, start, end):
        self._events.put(("gap", lecture_id, (start, end)))

    def prepare_slides(self, lecture_id, source):
        self._events.put(("slides", lecture_id, source))
        self._tick()

    def prepare_pdf(self, lecture_id, path):
        self._events.put(("pdf", lecture_id, str(path)))
        self._tick()

    def analyze_transcript_file(self, lecture_id, path):
        self._events.put(("transcript_file", lecture_id, str(path)))
        self._tick()

    def resume(self, lecture_id):
        """Analyze B's hydrated saved snapshot after reopening or a recoverable failure."""
        self._events.put(("resume", lecture_id, None))
        self._tick()

    def finalize(self, lecture_id, *, transcript_complete):
        """Explicit B/controller handoff after flush, including false on known incompleteness."""
        self._events.put(("finish", lecture_id, transcript_complete))
        self._tick()

    def finalize_for_export(self, lecture_id, path):
        self._events.put(("export", lecture_id, str(path)))
        self._tick()

    @Slot()
    def _tick(self):
        if self._closed or self._busy:
            return
        self._busy = True
        future = self._executor.submit(self._pump)

        def completed(result):
            try:
                more = result.result()
            except Exception:
                more = False
                self.processing_error.emit("", "interpretation_worker_failed", "Background processing failed; retry available", True)
            self._completed.emit(more)
        future.add_done_callback(completed)

    @Slot(bool)
    def _done(self, more):
        self._busy = False
        if self._closed:
            self.shutdown_complete.emit()
            return
        if (more or not self._events.empty()) and not self._closed:
            self._tick()

    def _pump(self):
        events = list(self._pending_events)
        self._pending_events.clear()
        while True:
            try:
                events.append(self._events.get_nowait())
            except Empty:
                break
        # Persist all received segments/gaps before any finalization/model work.
        events.sort(key=lambda e: e[0] not in ("segment", "gap", "resume"))
        for kind, lecture_id, value in events:
            self._lectures.add(lecture_id)
            try:
                if kind == "segment":
                    self.notes.accept_final_segment(lecture_id, value)
                elif kind == "gap":
                    self.notes.record_gap(lecture_id, *value)
                elif kind == "slides":
                    result = self.slides.prepare_slides(lecture_id, value, self.slide_progress.emit)
                    self.slides_ready.emit(lecture_id, result)
                elif kind == "pdf":
                    source = extract_pdf(value)
                    self.pdf_loaded.emit(lecture_id, source)
                    result = self.slides.prepare_slides(lecture_id, source, self.slide_progress.emit)
                    self.slides_ready.emit(lecture_id, result)
                elif kind == "transcript_file":
                    import re
                    from pathlib import Path
                    text = Path(value).read_text(encoding="utf-8")
                    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
                    if not paragraphs:
                        raise ValueError("The transcript file contains no speech")
                    segments = [Segment(id=stable_id(lecture_id, "imported-speech", sequence, paragraph),
                        lecture_id=lecture_id, sequence=sequence, text=paragraph, timing_quality="unknown")
                        for sequence, paragraph in enumerate(paragraphs)]
                    # Import all finalized evidence before waiting on any AI request.
                    # A slow/failed request must not hide the rest of the source file.
                    for segment in segments:
                        self.notes.accept_final_segment(lecture_id, segment)
                    self.state_updated.emit(lecture_id, self.notes.repo.load(lecture_id))
                    for segment in segments:
                        # Analyze incrementally using a cutoff, despite the whole file
                        # already being loaded. Future paragraphs are not analysis evidence.
                        self.notes.process_finalized_speech(lecture_id, through_segment_id=segment.id)
                        self.notes.generate_due_notes(lecture_id)
                        self.state_updated.emit(lecture_id, self.notes.repo.load(lecture_id))
                        self.analysis_progress.emit(lecture_id, {"processed_sequence": segment.sequence,
                            "backlog_segments": len(segments) - segment.sequence - 1})
                    result = self.notes.finish_lecture(lecture_id, transcript_complete=True)
                    self.state_updated.emit(lecture_id, result)
                    self.final_sections_ready.emit(lecture_id, result)
                elif kind == "finish":
                    # A failed segment write must be retried before claiming finalization readiness.
                    if any(e[1] == lecture_id and e[0] == "segment" for e in self._pending_events):
                        self._pending_events.append((kind, lecture_id, value))
                        continue
                    result = self.notes.finish_lecture(lecture_id, transcript_complete=value)
                    self.final_sections_ready.emit(lecture_id, result)
                elif kind == "export":
                    final = FinalNotesService(self.notes.repo, self.notes.ai, self.notes.settings).finalize(lecture_id)
                    self.final_notes_ready.emit(lecture_id, final, value)
            except Exception as exc:
                self.processing_error.emit(lecture_id, "interpretation_" + kind + "_failed",
                    f"{type(exc).__name__}: {safe_error_message(exc)}", True)
                if kind in ("segment", "gap", "finish", "slides"):
                    self._pending_events.append((kind, lecture_id, value))
        more = False
        for lecture_id in sorted(self._lectures):
            try:
                state = self.notes.process_finalized_speech(lecture_id)
                for section in state.sections:
                    self.section_updated.emit(lecture_id, section)
                self.analysis_progress.emit(lecture_id, {"processed_sequence": state.processed_sequence,
                    "backlog_segments": sum(s.sequence > state.processed_sequence for s in state.segments)})
                more |= any(s.sequence > state.processed_sequence for s in state.segments)
                self.notes.generate_due_notes(lecture_id)
                saved = self.notes.repo.load(lecture_id)
                self.notes_updated.emit(lecture_id, [n for n in saved.notes if not n.stale or n.user_edited])
                self.state_updated.emit(lecture_id, saved)
            except StaleSnapshot:
                # New input takes precedence; the next timer check retries the latest snapshot.
                pass
            except Exception as exc:
                state = self.notes.repo.load(lecture_id)
                state.processing_error = type(exc).__name__ + ": " + safe_error_message(exc)
                try:
                    self.notes.repo.commit(state, state.revision)
                except StaleSnapshot:
                    pass
                self.processing_error.emit(lecture_id, "interpretation_analysis_failed",
                    state.processing_error, True)
                more = False  # bounded timer retries, no tight failure loop
        return more

    def shutdown(self, wait=False):
        """Call on GUI thread. Use wait=True only outside an interactive GUI callback.

        Keep this QObject alive until active work completes when wait=False.
        Shut down after final_sections_ready if final notes must finish first.
        """
        self._closed = True
        self._timer.stop()
        if self._recording is not None:
            self._recording.transcript_final.disconnect(self.on_transcript_final)
            self._recording.gap_recorded.disconnect(self.on_gap)
        self._executor.shutdown(wait=wait, cancel_futures=True)
        if not self._busy:
            self.shutdown_complete.emit()
