import unittest

from interpretation.config import Settings
from interpretation.final_notes import FinalNotesService, final_markdown
from interpretation.incremental_notes import IncrementalNotes
from interpretation.models import FinalResponse, Span
from interpretation.repository import MemoryRepository, StaleSnapshot
from tests.test_interpretation import FakeAI, segment


class FinalNotesTest(unittest.TestCase):
    def setUp(self):
        self.repo, self.ai = MemoryRepository(), FakeAI()
        self.notes = IncrementalNotes(self.repo, self.ai)
        for i, text in enumerate(("Definition and formula.", "A worked example.", "Final qualification: units matter.")):
            self.notes.accept_final_segment("L", segment(i, text))
        self.notes.finish_lecture("L", transcript_complete=True)
        self.service = FinalNotesService(self.repo, self.ai)

    def test_final_synthesis_receives_original_trailing_speech_and_is_cached(self):
        final = self.service.finalize("L")
        payload = next(p for schema, p, images in self.ai.calls if schema is FinalResponse)
        text = " ".join(p["text"] for section in payload["lecture_sections"] for p in section["speech"])
        self.assertIn("Final qualification: units matter.", text)
        self.assertIn("Final organized notes", final_markdown(final, self.repo.load("L")))
        self.assertEqual(final.status, "complete")
        self.assertEqual(self.service.finalize("L"), final)
        self.assertEqual(sum(schema is FinalResponse for schema, _, _ in self.ai.calls), 1)

    def test_invented_source_is_rejected(self):
        original = self.ai.generate

        def invalid(schema, *args, **kwargs):
            result = original(schema, *args, **kwargs)
            if schema is FinalResponse:
                result.sections[0].blocks[0].source_spans = [Span(segment_id="invented", text_revision=1, start_offset=0, end_offset=1)]
            return result

        self.ai.generate = invalid
        with self.assertRaises(ValueError):
            self.service.finalize("L")
        self.assertIsNone(self.repo.load("L").final_notes)
        self.assertTrue(self.repo.load("L").notes)

    def test_source_change_during_generation_cannot_commit_old_final_notes(self):
        original = self.ai.generate

        def changed(schema, *args, **kwargs):
            result = original(schema, *args, **kwargs)
            if schema is FinalResponse:
                self.notes.accept_final_segment("L", segment(3, "Recovered trailing speech."))
            return result

        self.ai.generate = changed
        with self.assertRaises(StaleSnapshot):
            self.service.finalize("L")
        self.assertIsNone(self.repo.load("L").final_notes)

    def test_long_sections_use_original_source_chunks(self):
        service = FinalNotesService(self.repo, self.ai, Settings(text_budget=12))
        final = service.finalize("L")
        payloads = [p for schema, p, _ in self.ai.calls if schema is FinalResponse]
        self.assertGreater(len(payloads), 1)
        for payload in payloads:
            self.assertLessEqual(sum(len(p["text"]) for s in payload["lecture_sections"] for p in s["speech"]), 12)
        refs = [p for s in final.sections for b in s.blocks for p in b.source_spans]
        last = self.repo.load("L").segments[-1]
        self.assertTrue(any(p.segment_id == last.id and p.end_offset == len(last.text) for p in refs))

    def test_incomplete_coverage_is_labelled_and_user_edits_are_preserved(self):
        state = self.repo.load("L")
        state.transcript_complete = False
        state.gaps = [(10, 20)]
        state.notes[0].user_edited = True
        state.notes[0].markdown = "My exact annotation."
        self.repo.commit(state, state.revision)
        final = self.service.finalize("L")
        self.assertEqual(final.status, "partial")
        self.assertTrue(final.coverage_warnings)
        annotations = [b for s in final.sections for b in s.blocks if b.origin == "user_annotation"]
        self.assertEqual(annotations[0].markdown, "My exact annotation.")

    def test_finalization_requires_recording_and_section_processing_to_finish(self):
        self.notes.accept_final_segment("L", segment(3, "Recording resumed"))
        with self.assertRaises(ValueError):
            self.service.finalize("L")
