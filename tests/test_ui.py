"""Run the actual widgets and Part C bridge offscreen, without API/microphone access."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import threading
import time
import unittest
import itertools
import queue

from PySide6.QtCore import QObject, Signal, QTimer

from interpretation.config import Settings
from tests.test_interpretation import FakeAI, qt_app
from ui.backend import PartCBackend
from ui.main import LectureBuddyApp
from slide_deep_dive import ConceptBreakdown
from ui.gemini_stub import GeminiServiceStub
from live_recording.audio_transcription import TranscriptResult


class FakeMicrophone:
    def __init__(self):
        self.audio = queue.Queue()
        self.fail_open = False

    def open(self, device_id):
        if self.fail_open:
            raise PermissionError("Fixture microphone permission denied")

    def read(self, timeout):
        try:
            return self.audio.get(timeout=timeout)
        except queue.Empty:
            return b""

    def close(self):
        pass


class FakeTranscriber:
    def __init__(self):
        self.fail_finish = False

    def connect(self, on_result, on_error):
        self.on_result = on_result
        self.end_sample = 0

    def send_batch(self, packets):
        self.end_sample = packets[-1].end_sample
        self.on_result(TranscriptResult("Live draft", False))
        self.on_result(TranscriptResult("Recorded sentence.", True, (packets[0].start_sample, self.end_sample)))

    def finish(self, timeout):
        if self.fail_finish:
            raise TimeoutError("Fixture final transcription timed out")
        self.on_result(TranscriptResult("Final trailing sentence.", True, (self.end_sample, self.end_sample)))

    def close(self):
        pass


class FakeDeepDiveService(QObject):
    breakdown_ready = Signal(int, object)
    breakdown_failed = Signal(int, str)
    chat_ready = Signal(int, str)
    chat_failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.ids = itertools.count(1)
        self.breakdown_calls = []
        self.chat_calls = []
        self.defer_chat = False
        self.stopped = False

    def request_breakdown(self, ctx):
        rid = next(self.ids)
        self.breakdown_calls.append(ctx)
        result = ConceptBreakdown(summary="Fixture concept explanation", key_concepts=[],
            formulas=[], lecture_connection="", check_questions=[])
        QTimer.singleShot(0, lambda: self.breakdown_ready.emit(rid, result))
        return rid

    def ask(self, ctx, question, history):
        rid = next(self.ids)
        self.chat_calls.append((ctx, question, list(history)))
        if not self.defer_chat:
            QTimer.singleShot(0, lambda: self.chat_ready.emit(rid, "Fixture grounded answer"))
        return rid

    def shutdown(self):
        self.stopped = True


class UITest(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        self.app.setQuitOnLastWindowClosed(False)
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.ai = FakeAI()
        self.backend = PartCBackend(ai=self.ai, settings=Settings(analysis_seconds=600),
                                    output_root=self.root / "outputs")
        self.deep_dive = FakeDeepDiveService()
        self.microphone, self.transcriber = FakeMicrophone(), FakeTranscriber()
        self.recorder = GeminiServiceStub(source_factory=lambda: self.microphone,
                                          transcriber_factory=lambda: self.transcriber)
        self.window = LectureBuddyApp(self.backend, self.deep_dive, self.recorder)
        self.window.show()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), self.window.statusBar().currentMessage())

    def make_pdf(self):
        from PySide6.QtGui import QPainter
        from PySide6.QtPrintSupport import QPrinter
        path = self.root / "slides.pdf"
        printer = QPrinter()
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(path))
        painter = QPainter(printer)
        painter.drawText(100, 100, "Vectors and cosine similarity")
        printer.newPage()
        painter.drawText(100, 100, "A worked example")
        painter.end()
        return path

    def tearDown(self):
        self.window.close()
        self.wait_for(lambda: self.window._backend_closed)
        self.app.processEvents()
        self.directory.cleanup()

    def test_pdf_topics_transcript_notes_details_and_export(self):
        self.assertTrue(self.window.btn_record.isEnabled())
        self.assertNotIn("Perceptron", self.window.workspace.transcript_box.toPlainText())
        self.window.workspace.load_pdf(str(self.make_pdf()))
        self.assertFalse(self.window.workspace.btn_import.isEnabled())
        self.wait_for(lambda: not self.backend.preparing and self.backend.state.index is not None)
        state = self.backend.state
        self.assertEqual(state.index.status, "ready")
        self.assertEqual(self.window.workspace.total_slides, 2)
        self.assertEqual(self.window.workspace.sec_count.text(), "1 section")
        self.assertTrue(self.window.workspace.btn_transcript.isEnabled())
        self.assertTrue(all(t != threading.get_ident() for t in self.ai.call_threads))
        self.window.open_resource("subsection", state.index.subsections[0].id)
        self.assertIn("Angles between vectors", self.window.drilldown.content_box.toPlainText())
        self.assertNotIn("Linear Combination", self.window.drilldown.content_box.toPlainText())
        self.assertTrue(self.window.drilldown.chat_input.isEnabled())

        text = self.root / "speech.txt"
        text.write_text("A definition of similarity.\n\nA formula example and final qualification.", encoding="utf-8")
        self.window.workspace.transcriptRequested.emit(str(text))
        self.wait_for(lambda: not self.backend.analyzing and self.backend.state.finalized)
        self.assertIn("final qualification", self.window.workspace.transcript_box.toPlainText())
        self.assertTrue(self.backend.current_notes())
        self.assertTrue(self.window.btn_export.isEnabled())
        self.assertTrue((self.backend.output_dir / "notes.md").is_file())
        self.assertTrue((self.backend.output_dir / "state.json").is_file())
        # Imported speech must not replace the prepared lecture outline with
        # transcript discussion titles or open/closed statuses.
        row = self.window.workspace.sec_list.itemAt(0).widget()
        self.assertEqual(row.title_text, state.index.subsections[0].title)
        self.assertEqual(row.meta_text, "Pages 1, 2")
        row.clicked.emit(row.title_text, row.meta_text, row.body_text)
        self.assertEqual(self.window._selected_resource, ("subsection", state.index.subsections[0].id))
        self.window.open_resource("section", self.backend.state.sections[0].id)
        self.assertIn("Definition and example", self.window.drilldown.content_box.toPlainText())
        self.assertIn("A formula example and final qualification", self.window.drilldown.content_box.toPlainText())
        chat_context = self.deep_dive.breakdown_calls[-1]
        self.assertIn("final qualification", chat_context.transcript_provider())
        self.assertIn("Definition and example", chat_context.description)

        output_pdf = self.root / "notes.pdf"
        self.backend.export_pdf(output_pdf)
        from interpretation.pdf_input import extract_pdf
        exported = extract_pdf(output_pdf)
        self.assertIn("Lecture notes", exported.pages[0].text)
        self.assertIn("Definition and example", exported.pages[0].text)

    def test_main_chat_uses_slide_context_and_preserves_history_on_updates(self):
        self.window.workspace.load_pdf(str(self.make_pdf()))
        self.wait_for(lambda: not self.backend.preparing and self.backend.state.index is not None)
        group = self.backend.state.index.subsections[0]
        self.window.open_resource("subsection", group.id)
        self.wait_for(lambda: self.window.drilldown.breakdown_state == "ready")
        ctx = self.deep_dive.breakdown_calls[-1]
        self.assertTrue(ctx.slide_png.startswith(b"\x89PNG"))
        self.assertIn("cosine similarity", ctx.slide_text)
        self.assertEqual(ctx.transcript_provider(), "")
        self.window.drilldown.chat_input.setText("What does this formula mean?")
        self.window.drilldown.send_message()
        self.wait_for(lambda: self.window.drilldown.chat_history[-1][2] == "ok")
        self.assertIn("Fixture grounded answer", self.window.drilldown.chat_feed.toPlainText())
        self.assertEqual(self.deep_dive.chat_calls[-1][2], [])
        history = list(self.window.drilldown.chat_history)
        calls = len(self.deep_dive.breakdown_calls)
        self.window.show_state(self.backend.state)
        self.assertEqual(self.window.drilldown.chat_history, history)
        self.assertEqual(len(self.deep_dive.breakdown_calls), calls)
        self.window.drilldown.chat_input.setText("Give another explanation")
        self.window.drilldown.send_message()
        self.wait_for(lambda: self.window.drilldown.chat_history[-1][2] == "ok")
        self.assertEqual(self.deep_dive.chat_calls[-1][2], [("user", history[0][1]), ("model", history[1][1])])

    def test_late_main_chat_answer_does_not_populate_another_slide(self):
        self.window.workspace.load_pdf(str(self.make_pdf()))
        self.wait_for(lambda: not self.backend.preparing and self.backend.state.index is not None)
        first, second = self.backend.state.index.slides
        self.window.open_resource("slide", first.id)
        self.deep_dive.defer_chat = True
        self.window.drilldown.chat_input.setText("Explain the first slide")
        self.window.drilldown.send_message()
        old_request = self.window.drilldown.chat_request
        self.window.open_resource("slide", second.id)
        self.deep_dive.chat_ready.emit(old_request, "Answer to the old slide")
        self.assertEqual(self.window.drilldown.chat_history, [])
        self.assertNotIn("Answer to the old slide", self.window.drilldown.chat_feed.toPlainText())

    def test_bad_pdf_is_recoverable(self):
        path = self.root / "not-a-pdf.pdf"
        path.write_text("bad PDF", encoding="utf-8")
        self.window.workspace.load_pdf(str(path))
        self.wait_for(lambda: not self.backend.preparing)
        self.assertIn("failed", self.window.statusBar().currentMessage())
        self.assertTrue(self.window.workspace.btn_import.isEnabled())

    def test_real_recording_manager_feeds_ui_and_flushes_last_speech_into_notes(self):
        self.window.workspace.load_pdf(str(self.make_pdf()))
        self.wait_for(lambda: not self.backend.preparing and self.backend.state.index is not None)
        outline = self.window.workspace.sec_list.itemAt(0).widget().title_text
        self.window.toggle_recording()
        self.wait_for(lambda: self.window.workspace.trans_status.text() == "LIVE STREAM")
        self.recorder.transcript_interim.emit("Words still being recognized")
        self.assertIn("Words still being recognized", self.window.workspace.transcript_box.toPlainText())
        self.microphone.audio.put(b"\x01\x00" * 16000)
        self.wait_for(lambda: "Recorded sentence." in self.window.workspace.transcript_box.toPlainText())
        self.assertNotIn("Words still being recognized", self.window.workspace.transcript_box.toPlainText())
        # The transcript appears before the interpretation worker's periodic analysis.
        self.assertEqual(self.backend.state.segments, [])
        self.window.show_state(self.backend.state)
        self.assertIn("Recorded sentence.", self.window.workspace.transcript_box.toPlainText())
        ctx = self.backend.deep_dive_context("subsection", self.backend.state.index.subsections[0].id)
        self.assertIn("Recorded sentence.", ctx.transcript_provider())
        self.window.toggle_recording()
        self.assertTrue(self.window.is_finishing)
        self.wait_for(lambda: self.backend.state.finalized and not self.backend.analyzing)
        self.assertIn("Final trailing sentence.", self.window.workspace.transcript_box.toPlainText())
        self.assertEqual([s.sequence for s in self.backend.state.segments], [0, 1])
        self.assertTrue(self.backend.state.transcript_complete)
        self.assertTrue(self.backend.current_notes())
        self.assertEqual(self.window.workspace.sec_list.itemAt(0).widget().title_text, outline)
        self.assertTrue(self.window.btn_record.isEnabled())
        self.assertIn("Final trailing sentence.", (self.backend.output_dir / "transcript.txt").read_text())

    def test_microphone_failure_shows_error_and_allows_retry(self):
        self.microphone.fail_open = True
        self.window.toggle_recording()
        self.wait_for(lambda: not self.window.is_recording)
        self.assertIn("permission denied", self.window.workspace.transcript_box.toPlainText())
        self.assertFalse(self.backend.recording_active)
        self.assertTrue(self.window.btn_record.isEnabled())

    def test_transcription_finish_timeout_is_marked_partial(self):
        self.transcriber.fail_finish = True
        self.window.toggle_recording()
        self.microphone.audio.put(b"\x01\x00" * 16000)
        self.wait_for(lambda: bool(self.backend.live_segments))
        self.window.toggle_recording()
        self.wait_for(lambda: self.backend.state.finalized and not self.backend.analyzing)
        self.assertFalse(self.backend.state.transcript_complete)
        self.assertTrue(self.backend.current_notes()[0].coverage_warnings)

    def test_closing_active_recording_stops_capture_and_flushes_before_shutdown(self):
        self.window.toggle_recording()
        self.microphone.audio.put(b"\x01\x00" * 16000)
        self.wait_for(lambda: bool(self.backend.live_segments))
        start = time.monotonic()
        self.window.close()
        self.assertLess(time.monotonic() - start, 0.5)
        self.wait_for(lambda: self.window._backend_closed)
        self.assertTrue(self.backend.state.finalized)
        self.assertIn("Final trailing sentence.", (self.backend.output_dir / "transcript.txt").read_text())

    def test_failed_analysis_keeps_entire_transcript_and_retry_does_not_duplicate(self):
        self.window.workspace.load_pdf(str(self.make_pdf()))
        self.wait_for(lambda: not self.backend.preparing and self.backend.state.index is not None)

        def fail_analysis():
            raise ValueError("Fixture analysis failure")

        self.ai.on_analysis = fail_analysis
        path = self.root / "speech.txt"
        path.write_text("First paragraph.\n\nSecond paragraph.\n\nLast qualification.", encoding="utf-8")
        self.backend.import_transcript(str(path))
        self.wait_for(lambda: not self.backend.analyzing and len(self.backend.state.segments) == 3)
        self.wait_for(lambda: not self.backend.controller._busy)
        self.assertIn("Last qualification.", self.window.workspace.transcript_box.toPlainText())
        self.assertIn("Fixture analysis failure", self.window.statusBar().currentMessage())
        self.assertFalse(self.backend.state.finalized)

        self.ai.on_analysis = None
        self.backend.import_transcript(str(path))
        self.wait_for(lambda: not self.backend.analyzing and self.backend.state.finalized)
        self.assertEqual(len(self.backend.state.segments), 3)
        self.assertTrue(self.backend.current_notes())

    def test_close_waits_for_background_call_without_blocking_gui(self):
        entered, release = threading.Event(), threading.Event()
        original = self.ai.generate

        def slow_generate(*args, **kwargs):
            entered.set()
            release.wait(4)
            return original(*args, **kwargs)

        self.ai.generate = slow_generate
        self.window.workspace.load_pdf(str(self.make_pdf()))
        self.wait_for(entered.is_set)
        start = time.monotonic()
        self.window.close()
        try:
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertFalse(self.window._backend_closed)
        finally:
            release.set()
        self.wait_for(lambda: self.window._backend_closed)
