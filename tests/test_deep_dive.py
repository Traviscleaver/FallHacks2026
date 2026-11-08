"""Verify main's Gemini chat service using the real SDK and a local HTTP transport."""
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading
import time
import unittest

import httpx
from google import genai
from google.genai import types

from slide_deep_dive import ConceptBreakdown, DeepDiveContext, DeepDiveService
from tests.test_interpretation import qt_app


class DeepDiveTest(unittest.TestCase):
    def test_main_breakdown_and_chat_requests_use_context_and_background_worker(self):
        app = qt_app()
        calls, threads = [], []
        result = {"summary": "Similarity compares vector directions.", "key_concepts": [],
                  "formulas": [], "lecture_connection": "A worked example was discussed.",
                  "check_questions": ["What does similarity compare?"]}

        def handler(request):
            body = json.loads(request.content)
            calls.append(body)
            threads.append(threading.get_ident())
            text = json.dumps(result) if body["generationConfig"].get("responseMimeType") else "It compares their directions."
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "role": "model", "parts": [{"text": text}]}}]})

        client = genai.Client(api_key="fixture-key", http_options=types.HttpOptions(
            client_args={"transport": httpx.MockTransport(handler)}))
        service = DeepDiveService(model="fixture-model")
        service._client = client
        breakdowns, answers, errors = [], [], []
        service.breakdown_ready.connect(lambda rid, value: breakdowns.append((rid, value)))
        service.chat_ready.connect(lambda rid, text: answers.append((rid, text)))
        service.breakdown_failed.connect(lambda rid, text: errors.append(text))
        service.chat_failed.connect(lambda rid, text: errors.append(text))

        def wait_for(predicate):
            deadline = time.monotonic() + 5
            while not predicate() and not errors and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.005)
            self.assertTrue(predicate(), errors)

        try:
            ctx = DeepDiveContext(title="Cosine similarity", badge="SLIDE 1", description="Prepared slide material",
                slide_png=b"fixture-png", slide_text="Cosine formula", transcript_provider=lambda: "A worked vector example.")
            first = service.request_breakdown(ctx)
            wait_for(lambda: bool(breakdowns))
            self.assertEqual(breakdowns[0][0], first)
            self.assertIsInstance(breakdowns[0][1], ConceptBreakdown)
            second = service.ask(ctx, "What does it compare?", [("user", "Earlier question"), ("model", "Earlier answer")])
            wait_for(lambda: bool(answers))
            self.assertEqual(answers[0], (second, "It compares their directions."))
            encoded = json.dumps(calls[-1])
            for expected in ("Cosine formula", "A worked vector example.", "Earlier question", "Earlier answer", "What does it compare?"):
                self.assertIn(expected, encoded)
            self.assertTrue(any("inlineData" in p for c in calls[-1]["contents"] for p in c["parts"]))
            self.assertTrue(all(t != threading.get_ident() for t in threads))
            self.assertFalse(errors)
        finally:
            service.shutdown()
            client.close()
