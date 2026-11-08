import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import tempfile
import threading
import time
import unittest

from interpretation.config import Settings
from interpretation.incremental_notes import IncrementalNotes, full_span
from interpretation.models import (Analysis, Boundary, Formula, NoteResponse, NoteProposal,
    PageMetadata, Segment, SlideExtraction, SubsectionProposal, Visual)
from interpretation.models import FinalResponse, FinalSection, FinalBlock
from interpretation.repository import MemoryRepository, StaleSnapshot
from interpretation.slide_preparation import PDFInput, PageInput, SlidePreparation

_qt_app = None


def qt_app():
    global _qt_app
    from PySide6.QtWidgets import QApplication
    _qt_app = QApplication.instance() or QApplication([])
    return _qt_app


class FakeAI:
    def __init__(self):
        self.calls = []
        self.decisions = []
        self.fail_page = None
        self.invent_page = False
        self.on_notes = None
        self.on_analysis = None
        self.call_threads = []

    def generate(self, schema, instruction, payload, images=()):
        self.calls.append((schema, payload, images))
        self.call_threads.append(threading.get_ident())
        if schema is SlideExtraction:
            pages = payload["pages"]
            if any(p["pdf_page_number"] == self.fail_page for p in pages):
                raise ConnectionError("fixture failure")
            ids = [p["slide_id"] for p in pages]
            return SlideExtraction(
                pages=[PageMetadata(slide_id=i, title="", printed_slide_number="42") for i in ids],
                subsections=[SubsectionProposal(title="Vectors", summary="Angles between vectors",
                    slide_ids=["invented"] if self.invent_page else ids, key_terms=["cosine", "dot product"],
                    formulas=[Formula(slide_id=ids[0], latex=r"\frac{x\cdot y}{\|x\|\|y\|}",
                        plain_text="dot product / product of vector lengths", symbols=["x: vector", "y: vector"])],
                    visuals=[Visual(slide_id=ids[0], kind="diagram", description="Two vectors at an angle")])])
        if schema is Analysis:
            if self.on_analysis:
                self.on_analysis()
            decision = self.decisions.pop(0) if self.decisions else {}
            return Analysis(through_segment_id=payload["through_segment_id"],
                transition=decision.get("transition", "continue"), boundary=decision.get("boundary"),
                title="Cosine similarity", next_title="Euclidean distance", subsection_ids=[],
                match_status="off_slide", strength=decision.get("strength", "moderate"),
                rationale="Fixture evidence", supporting_spans=[{k: v for k, v in payload["speech"][-1].items() if k != "text"}])
        if schema is NoteResponse:
            if self.on_notes:
                callback, self.on_notes = self.on_notes, None
                callback()
            return NoteResponse(blocks=[NoteProposal(markdown="- Definition and example, with qualification.",
                source_spans=[{k: v for k, v in s.items() if k != "text"} for s in payload["speech"]],
                origin="lecture")])
        if schema is FinalResponse:
            return FinalResponse(title="Lecture notes", sections=[FinalSection(
                title=section["title"], lecture_section_ids=[section["id"]], blocks=[FinalBlock(
                    markdown="Definition and example, with qualification. Final organized notes.", origin="lecture",
                    source_spans=[{k: v for k, v in p.items() if k != "text"} for p in section["speech"]])])
                for section in payload["lecture_sections"]])
        raise AssertionError(schema)


def segment(seq, text, lecture="L", revision=1):
    return Segment(id=f"s{seq}", lecture_id=lecture, sequence=seq, text=text,
                   text_revision=revision, audio_range=(seq * 160000, (seq + 1) * 160000))


class ServicesTest(unittest.TestCase):
    def setUp(self):
        self.repo, self.ai = MemoryRepository(), FakeAI()
        self.settings = Settings(batch_segments=2, provisional_seconds=15)
        self.service = IncrementalNotes(self.repo, self.ai, self.settings)

    def add(self, *texts):
        state = self.repo.load("L")
        seq = max((s.sequence for s in state.segments), default=-1) + 1
        for text in texts:
            self.service.accept_final_segment("L", segment(seq, text))
            seq += 1

    def process_all(self):
        while any(s.sequence > self.repo.load("L").processed_sequence for s in self.repo.load("L").segments):
            self.service.process_finalized_speech("L")

    def test_recording_dataclass_without_revision_and_duplicate(self):
        @dataclass(frozen=True)
        class BSegment:
            id: str = "s0"
            lecture_id: str = "L"
            sequence: int = 0
            text: str = "Definition"
            audio_range: tuple = (0, 16000)
            timing_quality: str = "approximate"
        self.service.accept_final_segment("L", BSegment())
        self.service.accept_final_segment("L", BSegment())
        self.assertEqual(len(self.repo.load("L").segments), 1)
        self.assertEqual(self.repo.load("L").segments[0].text_revision, 1)

    def test_backlog_is_ordered_and_examples_stay_together(self):
        self.add("Definition", "Formula", "First example", "Second example", "Qualification")
        self.service.process_finalized_speech("L")
        self.assertEqual(self.repo.load("L").processed_sequence, 1)
        self.process_all()
        state = self.repo.load("L")
        self.assertEqual(len(state.sections), 1)
        self.assertEqual([s.segment_id for s in state.sections[0].spans], [f"s{i}" for i in range(5)])

    def test_candidate_then_confirm_at_earlier_boundary(self):
        self.add("Definition", "Example")
        self.process_all()
        self.add("Now distance")
        boundary = Boundary(segment_id="s2", text_revision=1, offset=0)
        self.ai.decisions.append({"transition": "candidate", "boundary": boundary})
        self.process_all()
        self.assertEqual(self.repo.load("L").sections[0].status, "closing")
        self.assertEqual(len(self.repo.load("L").sections), 1)
        self.add("Coordinate differences")
        self.ai.decisions.append({"transition": "confirm", "boundary": boundary})
        self.process_all()
        state = self.repo.load("L")
        self.assertEqual([s.status for s in state.sections], ["closed", "open"])
        self.assertEqual([p.segment_id for p in state.sections[0].spans], ["s0", "s1"])
        self.assertEqual([p.segment_id for p in state.sections[1].spans], ["s2", "s3"])
        self.service.generate_due_notes("L")
        count = len(self.repo.load("L").notes)
        self.service.generate_due_notes("L")
        self.assertEqual(len(self.repo.load("L").notes), count)

    def test_inside_chunk_boundary_uses_unicode_codepoints(self):
        text = "Vectors 😀.\nNow distance"
        self.add(text)
        boundary = Boundary(segment_id="s0", text_revision=1, offset=text.index("Now"))
        self.ai.decisions.append({"transition": "confirm", "boundary": boundary, "strength": "strong"})
        self.process_all()
        state = self.repo.load("L")
        left, right = state.sections
        self.assertEqual(left.spans[0].end_offset, text.index("Now"))
        self.assertEqual(right.spans[0].start_offset, text.index("Now"))
        self.assertEqual(state.segments[0].text, text)

    def test_first_topic_boundary_at_start_continues_without_empty_section(self):
        self.add("First discussion")
        boundary = Boundary(segment_id="s0", text_revision=1, offset=0)
        self.ai.decisions.append({"transition": "confirm", "boundary": boundary, "strength": "strong"})
        self.process_all()
        state = self.repo.load("L")
        self.assertEqual(state.processed_sequence, 0)
        self.assertEqual(len(state.sections), 1)
        self.assertEqual(state.sections[0].spans, [full_span(state.segments[0])])
        self.assertEqual(state.alignments[-1].decision.transition, "confirm")
        self.assertEqual(state.alignments[-1].applied_transition, "continue")
        self.assertTrue(state.alignments[-1].boundary_warning)
        # Repeating that proposal later must not create an empty section either.
        self.add("Another example")
        self.ai.decisions.append({"transition": "confirm", "boundary": boundary})
        self.process_all()
        self.assertEqual(len(self.repo.load("L").sections), 1)

    def test_candidate_at_first_topic_start_does_not_leave_pending_empty_split(self):
        self.add("First discussion")
        self.ai.decisions.append({"transition": "candidate", "boundary": Boundary(segment_id="s0", text_revision=1, offset=0)})
        self.process_all()
        section = self.repo.load("L").sections[0]
        self.assertEqual(section.status, "open")
        self.assertIsNone(section.pending_boundary)

    def test_weak_unconfirmed_transition_is_proposed_then_confirmed(self):
        self.add("First topic", "Now a different topic")
        boundary = Boundary(segment_id="s1", text_revision=1, offset=0)
        self.ai.decisions.append({"transition": "confirm", "boundary": boundary})
        self.process_all()
        self.assertEqual(self.repo.load("L").sections[0].status, "closing")
        self.add("Further explanation of the different topic")
        self.ai.decisions.append({"transition": "confirm", "boundary": boundary})
        self.process_all()
        self.assertEqual([s.status for s in self.repo.load("L").sections], ["closed", "open"])

    def test_whitespace_only_tail_is_not_a_new_section(self):
        self.add("First discussion   ")
        self.ai.decisions.append({"transition": "confirm", "strength": "strong",
            "boundary": Boundary(segment_id="s0", text_revision=1, offset=len("First discussion"))})
        state = self.service.finish_lecture("L", transcript_complete=True)
        self.assertEqual(len(state.sections), 1)
        self.assertTrue(state.notes)
        self.assertEqual(state.sections[0].spans[0].end_offset, len(state.segments[0].text))

    def test_open_section_has_sources_before_notes(self):
        self.add("An off-slide explanation")
        self.process_all()
        state = self.repo.load("L")
        self.assertTrue(state.sections[0].spans)
        self.assertFalse(state.sections[0].subsection_ids)
        self.assertFalse(state.notes)

    def test_end_includes_trailing_speech_and_is_idempotent(self):
        self.add("Definition", "Example", "Final qualification")
        state = self.service.finish_lecture("L", transcript_complete=True)
        self.assertEqual(state.processed_sequence, 2)
        self.assertEqual(state.sections[-1].close_reason, "recording_ended")
        self.assertEqual(state.notes[-1].status, "section_final")
        self.assertEqual(state.notes[-1].source_spans[-1].segment_id, "s2")
        count = len(state.notes)
        state = self.service.finish_lecture("L", transcript_complete=True)
        self.assertEqual(len(state.notes), count)

    def test_partial_finalization_warns_and_can_be_upgraded(self):
        self.add("Remaining speech")
        state = self.service.finish_lecture("L", transcript_complete=False)
        self.assertTrue(state.notes[-1].coverage_warnings)
        state = self.service.finish_lecture("L", transcript_complete=True)
        self.assertTrue(state.transcript_complete)
        self.assertFalse(state.notes[-1].coverage_warnings)

    def test_stale_note_cannot_commit(self):
        self.add("Definition")
        self.process_all()
        section = self.repo.load("L").sections[0]
        self.ai.on_notes = lambda: self.add("New speech during generation")
        with self.assertRaises(StaleSnapshot):
            self.service.generate_section_notes("L", section.id, section.revision)
        self.assertFalse(self.repo.load("L").notes)
        self.assertEqual(len(self.repo.load("L").segments), 2)

    def test_stale_analysis_cannot_commit(self):
        self.add("Definition")
        self.ai.on_analysis = lambda: self.add("New evidence")
        with self.assertRaises(StaleSnapshot):
            self.service.process_finalized_speech("L")
        self.assertEqual(self.repo.load("L").processed_sequence, -1)
        self.assertFalse(self.repo.load("L").sections)

    def test_correction_replays_and_preserves_user_edits(self):
        self.add("Incorrect words")
        self.process_all()
        section = self.repo.load("L").sections[0]
        self.service.generate_section_notes("L", section.id)
        state = self.repo.load("L")
        state.notes[0].user_edited = True
        self.repo.commit(state, state.revision)
        self.service.accept_final_segment("L", segment(0, "Corrected words", revision=2))
        state = self.repo.load("L")
        self.assertEqual(state.processed_sequence, -1)
        self.assertTrue(state.notes[0].user_edited)
        self.assertTrue(state.notes[0].stale)
        self.process_all()
        self.assertEqual(self.repo.load("L").sections[0].spans[0].text_revision, 2)

    def test_provisional_notes_and_gap_warning(self):
        self.add("Definition", "Example")
        self.process_all()
        self.service.record_gap("L", 100, 300)
        self.service.generate_due_notes("L")
        state = self.repo.load("L")
        self.assertEqual(state.notes[0].status, "provisional")
        note_call = next(payload for schema, payload, images in self.ai.calls if schema is NoteResponse)
        self.assertEqual(note_call["audio_gaps_samples"], [(100, 300)])

    def test_future_boundary_rejected_without_moving_cursor(self):
        self.add("Definition")
        self.ai.decisions.append({"transition": "candidate", "boundary": Boundary(segment_id="future", text_revision=1, offset=0)})
        with self.assertRaises(ValueError):
            self.process_all()
        self.assertEqual(self.repo.load("L").processed_sequence, -1)

    def test_missing_sequence_is_not_skipped(self):
        self.service.accept_final_segment("L", segment(1, "Missing prior segment"))
        with self.assertRaises(ValueError):
            self.process_all()

    def test_cross_lecture_segment_rejected(self):
        with self.assertRaises(ValueError):
            self.service.accept_final_segment("L", segment(0, "Wrong lecture", lecture="other"))

    def test_repository_snapshot_is_independent_and_cas(self):
        old = self.repo.load("L")
        self.add("Definition")
        self.assertFalse(old.segments)
        with self.assertRaises(StaleSnapshot):
            self.repo.commit(old, old.revision)


class SlidesTest(unittest.TestCase):
    def setUp(self):
        self.repo, self.ai = MemoryRepository(), FakeAI()
        self.settings = Settings(slide_batch_pages=2)
        self.service = SlidePreparation(self.repo, self.ai, self.settings)
        self.source = PDFInput(sha256(b"fixture-pdf").hexdigest(), tuple(
            PageInput(i, "" if i == 2 else f"page {i}", b"\x89PNG\r\n\x1a\nfixture", f"asset-{i}") for i in range(1, 4)))

    def test_visual_index_stable_ids_formula_and_overlap(self):
        index = self.service.prepare_slides("L", self.source)
        self.assertEqual(index.status, "ready")
        self.assertEqual(len(index.subsections), 1)
        self.assertEqual(set(index.subsections[0].slide_ids), {s.id for s in index.slides})
        self.assertEqual(index.slides[1].extracted_text, "")
        self.assertEqual(index.slides[1].title, "Page 2")
        self.assertEqual(index.slides[0].printed_slide_number, "42")
        self.assertIn("dot product", index.subsections[0].search_text)
        self.assertIn(r"\frac", index.subsections[0].formulas[0].latex)
        self.assertEqual(index.glossary, ["cosine", "dot product"])
        count = len(self.ai.calls)
        cached = self.service.prepare_slides("L", self.source)
        self.assertEqual(len(self.ai.calls), count)
        self.assertEqual([s.id for s in cached.slides], [s.id for s in index.slides])
        self.assertTrue(all(images for _, _, images in self.ai.calls))

    def test_partial_failure_retry_preserves_success_and_ids(self):
        self.ai.fail_page = 3
        partial = self.service.prepare_slides("L", self.source)
        self.assertEqual(partial.status, "partial")
        self.assertEqual([s.status for s in partial.slides], ["ready", "ready", "failed"])
        self.ai.fail_page = None
        ready = self.service.prepare_slides("L", self.source)
        self.assertEqual(ready.status, "ready")
        self.assertEqual([s.id for s in ready.slides], [s.id for s in partial.slides])

    def test_invented_page_fails_and_keeps_raw_text(self):
        self.ai.invent_page = True
        index = self.service.prepare_slides("L", self.source)
        self.assertEqual(index.status, "failed")
        self.assertEqual(index.slides[0].extracted_text, "page 1")
        self.assertFalse(index.subsections)

    def test_replacing_pdf_changes_page_identity(self):
        first = self.service.prepare_slides("L", self.source)
        second = self.service.prepare_slides("L", PDFInput(sha256(b"replacement").hexdigest(), self.source.pages))
        self.assertNotEqual(first.slides[0].id, second.slides[0].id)

    def test_invalid_page_order_or_missing_visual_rejected(self):
        for pages in ((PageInput(2, "x", b"png"),), (PageInput(1, "", b""),)):
            with self.assertRaises(ValueError):
                self.service.prepare_slides("L", PDFInput(self.source.pdf_hash, pages))


class QtPDFTest(unittest.TestCase):
    def test_actual_pdf_page_count_rendering_and_text(self):
        from PySide6.QtGui import QGuiApplication, QPainter
        from PySide6.QtPrintSupport import QPrinter
        from interpretation.pdf_input import extract_pdf
        app = qt_app()
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "fixture.pdf")
            printer = QPrinter()
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(path)
            painter = QPainter(printer)
            painter.drawText(100, 100, "Lecture fixture: printed slide 42")
            printer.newPage()
            painter.drawLine(100, 100, 300, 300)  # visual page with no extractable text
            painter.end()
            source = extract_pdf(path)
            self.assertEqual(len(source.pages), 2)
            self.assertEqual(source.pages[0].page_number, 1)
            self.assertIn("42", source.pages[0].text)
            self.assertEqual(source.pages[1].text, "")
            self.assertTrue(source.pages[1].image_png.startswith(b"\x89PNG"))
            self.assertEqual(source.pdf_hash, sha256(Path(path).read_bytes()).hexdigest())
            # Exercise the same harness the user will run, with real PDF extraction
            # and a deterministic fake AI instead of a paid API request.
            from interpretation.smoke_test import run_smoke
            transcript = Path(directory) / "transcript.txt"
            transcript.write_text("A definition.\n\nA formula and example.\n\nA final qualification.", encoding="utf-8")
            output = Path(directory) / "results"
            self.assertEqual(run_smoke(path, output, transcript, ai=FakeAI(), settings=Settings()), 0)
            self.assertTrue((output / "slide_index.json").is_file())
            self.assertTrue((output / "notes.md").is_file())
            self.assertTrue((output / "state.json").is_file())
            self.assertEqual(len(list((output / "pages").glob("*.png"))), 2)
        self.assertIsNotNone(app)


class WorkerTest(unittest.TestCase):
    def test_real_recording_signals_coalesce_and_finalize_off_gui(self):
        from PySide6.QtCore import QObject, Signal
        from interpretation.workers import InterpretationController

        class RecordingSignals(QObject):
            transcript_final = Signal(str, object)
            gap_recorded = Signal(str, int, int)

        app = qt_app()
        repo, ai, recorder = MemoryRepository(), FakeAI(), RecordingSignals()
        settings = Settings(analysis_seconds=600, batch_segments=2)
        notes = IncrementalNotes(repo, ai, settings)
        controller = InterpretationController(SlidePreparation(repo, ai, settings), notes, recorder)
        ready, errors = [], []
        controller.final_sections_ready.connect(lambda lid, state: ready.append(state))
        controller.processing_error.connect(lambda *args: errors.append(args))

        def wait_for(predicate):
            deadline = time.monotonic() + 5
            while not predicate() and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            self.assertTrue(predicate(), errors)

        try:
            recorder.transcript_final.emit("L", segment(0, "Definition"))
            recorder.transcript_final.emit("L", segment(1, "Formula"))
            recorder.transcript_final.emit("L", segment(2, "Last example"))
            recorder.gap_recorded.emit("L", 10, 20)
            app.processEvents()
            self.assertFalse(ai.calls)  # Signals enqueue; no per-word model requests.
            controller._tick()
            wait_for(lambda: repo.load("L").processed_sequence == 2 and not controller._busy)
            controller.finalize("L", transcript_complete=False)
            wait_for(lambda: bool(ready))
            self.assertEqual(ready[0].sections[-1].spans[-1].segment_id, "s2")
            self.assertTrue(ready[0].notes[-1].coverage_warnings)
            self.assertTrue(all(t != threading.get_ident() for t in ai.call_threads))
            self.assertFalse(errors)
        finally:
            controller.shutdown(wait=True)
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
