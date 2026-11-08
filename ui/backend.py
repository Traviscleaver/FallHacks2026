"""Small MVP bridge from the existing Part C services to A's widgets.

Main's recording facade supplies finalized speech. Text-file import remains
available for demonstrating notes independently of the microphone.
"""
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Signal

from interpretation.config import Settings
from interpretation.gemini_adapter import GeminiAdapter
from interpretation.incremental_notes import IncrementalNotes
from interpretation.repository import MemoryRepository
from interpretation.slide_preparation import SlidePreparation
from interpretation.workers import InterpretationController
from interpretation.final_notes import final_markdown


class PartCBackend(QObject):
    pdf_ready = Signal(object)
    state_ready = Signal(object)
    status_changed = Signal(str)
    capabilities_changed = Signal()
    closed = Signal()
    export_finished = Signal(str)

    def __init__(self, ai=None, settings=None, output_root="test_outputs", parent=None):
        super().__init__(parent)
        self.settings = settings or Settings.from_env()
        self.ai = ai or GeminiAdapter(self.settings)
        self.repo = MemoryRepository()
        self.controller = InterpretationController(
            SlidePreparation(self.repo, self.ai, self.settings),
            IncrementalNotes(self.repo, self.ai, self.settings), parent=self)
        self.lecture_id = ""
        self.output_root = Path(output_root)
        self.output_dir = None
        self.state = None
        self.pdf_source = None
        self.preparing = False
        self.analyzing = False
        self.exporting = False
        self.transcript_path = None
        self.closing = False
        self.recording_active = False
        self.live_segments = []
        self._next_live_sequence = 0
        self.controller.pdf_loaded.connect(self._pdf_loaded)
        self.controller.slides_ready.connect(self._slides_ready)
        self.controller.slide_progress.connect(self._progress)
        self.controller.analysis_progress.connect(self._analysis_progress)
        self.controller.state_updated.connect(self._state_updated)
        self.controller.final_sections_ready.connect(self._finished)
        self.controller.final_notes_ready.connect(self._export_ready)
        self.controller.processing_error.connect(self._error)
        self.controller.shutdown_complete.connect(self._shutdown_complete)

    def import_pdf(self, path):
        if self.preparing or self.analyzing or self.exporting or self.closing or self.recording_active:
            self.status_changed.emit("Please wait for the current operation to finish.")
            return
        self.lecture_id = "ui-" + uuid4().hex
        self.output_dir = self.output_root / self.lecture_id
        self.state = self.repo.load(self.lecture_id)
        self.pdf_source = None
        self.transcript_path = None
        self.live_segments = []
        self._next_live_sequence = 0
        self.preparing = True
        self.state_ready.emit(self.state)
        self.capabilities_changed.emit()
        self.status_changed.emit("Opening slides…")
        self.controller.prepare_pdf(self.lecture_id, path)

    def import_transcript(self, path):
        if not self.lecture_id or self.preparing or self.analyzing or self.exporting or self.closing or self.recording_active:
            self.status_changed.emit("Import slides and wait for preparation before adding a transcript.")
            return
        if self.state and self.state.segments and Path(path).resolve() != self.transcript_path:
            self.status_changed.emit("Import the PDF again to start a new transcript test.")
            return
        self.analyzing = True
        self.transcript_path = Path(path).resolve()
        self.capabilities_changed.emit()
        self.status_changed.emit("Generating section notes from the transcript…")
        self.controller.analyze_transcript_file(self.lecture_id, path)

    def begin_live_recording(self):
        if self.analyzing or self.exporting or self.closing or self.recording_active:
            return False
        if self.transcript_path is not None:
            # Continue after a text-file demo using canonical sequence ordering.
            self.live_segments = list(self.state.segments)
            self._next_live_sequence = max((s.sequence for s in self.live_segments), default=-1) + 1
            self.transcript_path = None
        if not self.lecture_id:
            self.lecture_id = "ui-" + uuid4().hex
            self.output_dir = self.output_root / self.lecture_id
            self.state = self.repo.load(self.lecture_id)
            self.state_ready.emit(self.state)
        self.recording_active = True
        self.capabilities_changed.emit()
        return True

    def cancel_live_start(self):
        self.recording_active = False
        self.capabilities_changed.emit()

    def accept_live_segment(self, original, offset_seconds):
        from interpretation.models import Segment
        if any(s.id == original.id for s in self.live_segments):
            return
        offset = int(offset_seconds * 16000)
        segment = Segment(id=original.id, lecture_id=self.lecture_id, sequence=self._next_live_sequence,
            text=original.text, audio_range=(original.audio_range[0] + offset, original.audio_range[1] + offset),
            timing_quality=original.timing_quality if offset == 0 else "approximate")
        self._next_live_sequence += 1
        self.live_segments.append(segment)
        self.controller.on_transcript_final(self.lecture_id, segment)

    def accept_live_gap(self, start, end):
        self.controller.on_gap(self.lecture_id, start, end)

    def finish_live_recording(self, transcript_complete):
        self.recording_active = False
        if self.closing:
            return
        # Preserve recognized speech before any remaining AI work can fail.
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / "transcript.txt").write_text(
                "\n\n".join(s.text for s in self.live_segments), encoding="utf-8")
        except OSError:
            self.status_changed.emit("Recording stopped, but saving the transcript failed.")
        self.analyzing = True
        self.status_changed.emit("Recording stopped. Preparing the remaining section notes…")
        self.capabilities_changed.emit()
        self.controller.finalize(self.lecture_id, transcript_complete=transcript_complete)

    def _pdf_loaded(self, lecture_id, source):
        if lecture_id == self.lecture_id:
            self.pdf_source = source
            self.pdf_ready.emit(source)

    def _progress(self, lecture_id, ready, total):
        if lecture_id == self.lecture_id:
            self.status_changed.emit(f"Preparing slides: {ready}/{total} ready…")

    def _analysis_progress(self, lecture_id, progress):
        if lecture_id == self.lecture_id and self.analyzing and self.state:
            done = progress["processed_sequence"] + 1
            self.status_changed.emit(f"Preparing section notes: analyzed {done}/{len(self.state.segments)} paragraphs…")

    def _slides_ready(self, lecture_id, index):
        if lecture_id != self.lecture_id:
            return
        self.preparing = False
        self._state_updated(lecture_id, self.repo.load(lecture_id))
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / "slide_index.json").write_text(index.model_dump_json(indent=2), encoding="utf-8")
        except OSError:
            self.status_changed.emit("Slides prepared, but saving the index failed.")
            self.capabilities_changed.emit()
            return
        self.status_changed.emit(f"Slides {index.status}: {len(index.slides)} pages, "
                                 f"{len(index.subsections)} topics. Import a transcript to generate notes.")
        self.capabilities_changed.emit()

    def _state_updated(self, lecture_id, state):
        if lecture_id != self.lecture_id:
            return
        self.state = state
        self.state_ready.emit(state)
        self.capabilities_changed.emit()

    def _finished(self, lecture_id, state):
        if lecture_id != self.lecture_id:
            return
        self.analyzing = False
        self._state_updated(lecture_id, state)
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / "state.json").write_text(state.model_dump_json(indent=2), encoding="utf-8")
            (self.output_dir / "notes.md").write_text(self.notes_markdown(), encoding="utf-8")
            (self.output_dir / "transcript.txt").write_text("\n\n".join(s.text for s in state.segments), encoding="utf-8")
            self.status_changed.emit(f"Notes ready. Saved to {self.output_dir.resolve()}")
        except OSError:
            self.status_changed.emit("Notes ready, but saving failed. You can still export the PDF.")
        self.capabilities_changed.emit()

    def _error(self, lecture_id, code, message, retryable):
        if lecture_id and lecture_id != self.lecture_id:
            return
        self.preparing = self.analyzing = self.exporting = False
        self.status_changed.emit(f"Processing failed: {message}")
        self.capabilities_changed.emit()

    def current_notes(self):
        return [n for n in self.state.notes if not n.stale or n.user_edited] if self.state else []

    def notes_markdown(self):
        if not self.state:
            return ""
        pages = {s.id: s.page_number for s in self.state.index.slides} if self.state.index else {}
        segments = {s.id: s.sequence + 1 for s in self.state.segments}
        blocks = ["# Lecture notes\n"]
        notes = self.current_notes()
        for section in self.state.sections:
            section_notes = [n for n in notes if n.section_id == section.id]
            if not section_notes:
                continue
            blocks.append(f"## {section.title}\n")
            for note in section_notes:
                blocks.append(note.markdown)
                if note.status == "provisional":
                    blocks.append("*Provisional notes.*")
                references = [f"paragraph {segments[s.segment_id]}" for s in note.source_spans]
                references += [f"PDF page {pages[sid]}" for sid in note.slide_ids]
                if references:
                    blocks.append("Sources: " + ", ".join(dict.fromkeys(references)))
                blocks.extend("> " + warning for warning in note.coverage_warnings)
        return "\n\n".join(blocks)

    def detail(self, kind, entity_id):
        state = self.state
        if not state:
            return "Lecture", "PENDING", "Import a PDF first."
        groups = state.index.subsections if state.index else []
        if kind == "subsection":
            group = next((g for g in groups if g.id == entity_id), None)
            if group is None:
                return "Slide topic", "PENDING", "This topic is no longer available."
            title = group.title
            selected_groups = [group]
            sections = [s for s in state.sections if group.id in s.subsection_ids]
        elif kind == "slide":
            slide = next((s for s in state.index.slides if s.id == entity_id), None) if state.index else None
            if slide is None:
                return "Slide", "PENDING", "Slide preparation is still in progress."
            title = slide.title
            selected_groups = [g for g in groups if slide.id in g.slide_ids]
            group_ids = {g.id for g in selected_groups}
            sections = [s for s in state.sections if group_ids & set(s.subsection_ids)]
        else:
            sections = [s for s in state.sections if s.id == entity_id]
            title = sections[0].title if sections else "Lecture section"
            ids = {gid for s in sections for gid in s.subsection_ids}
            selected_groups = [g for g in groups if g.id in ids]
        page_numbers = {s.id: s.page_number for s in state.index.slides} if state.index else {}
        body = ["## Slide material"]
        for group in selected_groups:
            body.extend([f"### {group.title}", group.summary,
                         "PDF pages: " + ", ".join(str(page_numbers[sid]) for sid in group.slide_ids)])
            body.extend(group.definitions)
            for formula in group.formulas:
                body.append(f"Formula: `{formula.latex or formula.plain_text}`\n\n{formula.plain_text}")
                body.extend(formula.symbols)
                if formula.conditions:
                    body.append("Conditions: " + formula.conditions)
            body.extend(v.description for v in group.visuals)
        if not selected_groups:
            body.append("No slide material linked to this discussion.")
        body.append("## Section notes")
        section_ids = {s.id for s in sections}
        notes = [n for n in self.current_notes() if n.section_id in section_ids]
        body.extend(n.markdown for n in notes)
        if not notes:
            body.append("No related notes available yet.")
        body.append("## Related transcript")
        by_id = {s.id: s for s in state.segments}
        for section in sections:
            body.append(f"### {section.title} ({section.status})")
            body.extend(by_id[p.segment_id].text[p.start_offset:p.end_offset] for p in section.spans)
        if not sections:
            body.append("No related transcript found so far.")
        return title, "LECTURE SECTION" if kind == "section" else "SLIDE MATERIAL", "\n\n".join(body)

    def export_pdf(self, path):
        if self.recording_active or self.preparing or self.analyzing or self.exporting or self.closing:
            raise ValueError("Wait for recording and note processing to finish before exporting")
        if not self.current_notes():
            raise ValueError("Generate lecture notes before exporting")
        self.exporting = True
        self.capabilities_changed.emit()
        self.status_changed.emit("Preparing final study notes for PDF export…")
        self.controller.finalize_for_export(self.lecture_id, path)

    def _export_ready(self, lecture_id, final, path):
        if lecture_id != self.lecture_id:
            return
        try:
            self.state = self.repo.load(lecture_id)
            markdown = final_markdown(final, self.state)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / "final_notes.json").write_text(final.model_dump_json(indent=2), encoding="utf-8")
            (self.output_dir / "final_notes.md").write_text(markdown, encoding="utf-8")
            (self.output_dir / "state.json").write_text(self.state.model_dump_json(indent=2), encoding="utf-8")
            self._write_pdf(path, markdown)
            self.status_changed.emit(f"Final notes exported to {path}")
            self.export_finished.emit(path)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status_changed.emit(f"PDF export failed: {exc}. Prepared notes are retained for retry.")
        finally:
            self.exporting = False
            self.capabilities_changed.emit()

    def _write_pdf(self, path, markdown):
        from PySide6.QtGui import QTextDocument
        from PySide6.QtPrintSupport import QPrinter
        document = QTextDocument()
        document.setMarkdown(markdown, QTextDocument.MarkdownFeature.MarkdownNoHTML)
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(path))
        document.print_(printer)
        if not Path(path).is_file() or Path(path).stat().st_size == 0:
            raise OSError("Could not write PDF")

    def deep_dive_context(self, kind, entity_id):
        """Adapt Part C records to main's existing deep-dive/chat contract.

        The transcript callback captures plain source text, so Gemini workers
        never read a widget or a mutable live repository snapshot.
        """
        from slide_deep_dive import DeepDiveContext
        title, badge, description = self.detail(kind, entity_id)
        state = self.state
        if state is None or state.index is None:
            return DeepDiveContext(title=title, badge=badge, description=description)
        groups = state.index.subsections
        slide_ids = set()
        if kind == "section":
            sections = [s for s in state.sections if s.id == entity_id]
            group_ids = {gid for section in sections for gid in section.subsection_ids}
        else:
            group_ids = ({entity_id} if kind == "subsection" else
                         {g.id for g in groups if entity_id in g.slide_ids})
            sections = [s for s in state.sections if group_ids & set(s.subsection_ids)]
        for group in groups:
            if group.id in group_ids:
                slide_ids.update(group.slide_ids)
        if kind == "slide":
            slide_ids = {entity_id}
        slides = [s for s in state.index.slides if s.id in slide_ids]
        by_id = {s.id: s for s in state.segments}
        transcript = "\n\n".join(
            f"[Paragraph {by_id[p.segment_id].sequence + 1}] "
            + by_id[p.segment_id].text[p.start_offset:p.end_offset]
            for section in sections for p in section.spans)
        if self.live_segments:
            transcript = "Available finalized transcript; slide alignment may still be pending.\n\n" + "\n\n".join(
                f"[Segment {s.sequence + 1}] {s.text}" for s in self.live_segments)
        primary = slides[0] if slides else None
        png = None
        if primary and self.pdf_source:
            png = self.pdf_source.pages[primary.page_number - 1].image_png
        return DeepDiveContext(title=title, badge=badge, description=description,
            slide_png=png, slide_text="\n\n".join(f"PDF page {s.page_number}:\n{s.extracted_text}" for s in slides),
            slide_number=primary.page_number if primary else None,
            total_slides=len(state.index.slides), transcript_provider=lambda text=transcript: text)

    def shutdown(self):
        if not self.closing:
            self.closing = True
            self.controller.shutdown(wait=False)

    def _shutdown_complete(self):
        close = getattr(self.ai, "close", None)
        if close:
            close()
        self.closed.emit()
