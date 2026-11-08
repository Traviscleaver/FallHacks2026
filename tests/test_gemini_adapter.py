"""Real SDK serialization against an in-process HTTP transport; no credentials/network."""
import json
import unittest
from unittest.mock import patch

try:
    import httpx
    from google import genai
    from google.genai import types
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False

from interpretation.config import Settings
from interpretation.gemini_adapter import GeminiAdapter, safe_error_message, transport_schema
from interpretation.models import NoteResponse, SlideExtraction, Analysis, FinalResponse


class ErrorReportingTest(unittest.TestCase):
    def test_api_key_is_redacted_from_message(self):
        class APIError(Exception):
            message = "Invalid key fixture-secret"
        with patch.dict("os.environ", {"GEMINI_KEY": "fixture-secret"}):
            self.assertEqual(safe_error_message(APIError()), "Invalid key [REDACTED]")


@unittest.skipUnless(SDK_AVAILABLE, "google-genai is not installed in this Python environment")
class GeminiSDKTest(unittest.TestCase):
    def test_real_sdk_serializes_every_response_schema(self):
        for schema in (SlideExtraction, Analysis, NoteResponse, FinalResponse):
            config = types.GenerateContentConfig(response_mime_type="application/json",
                                                 response_json_schema=transport_schema(schema))
            self.assertIsNotNone(config.response_json_schema)

    def client(self, handler):
        return genai.Client(api_key="fixture-key", http_options=types.HttpOptions(
            client_args={"transport": httpx.MockTransport(handler)},
            retry_options=types.HttpRetryOptions(attempts=1)))

    def response(self, text, finish="STOP"):
        return httpx.Response(200, json={"candidates": [{"finishReason": finish,
            "content": {"role": "model", "parts": [{"text": text}]}}]})

    def test_request_validation_images_and_response(self):
        requests = []
        result = {"blocks": [{"markdown": "A slide definition", "source_spans": [],
                              "slide_ids": ["slide-1"], "origin": "slide_background"}]}

        def handler(request):
            requests.append(json.loads(request.content))
            return self.response(json.dumps(result))

        client = self.client(handler)
        try:
            adapter = GeminiAdapter(Settings(retries=0), client=client)
            response = adapter.generate(NoteResponse, "Fixture instruction", {"lecture_id": "L"},
                                        (b"fixture-png",))
            self.assertEqual(response.blocks[0].slide_ids, ["slide-1"])
            request = requests[0]
            self.assertEqual(request["generationConfig"]["responseMimeType"], "application/json")
            self.assertNotIn("responseSchema", request["generationConfig"])
            self.assertEqual(request["generationConfig"]["responseJsonSchema"], transport_schema(NoteResponse))
            wire_schema = json.dumps(request["generationConfig"]["responseJsonSchema"])
            self.assertNotIn('"additionalProperties"', wire_schema)
            self.assertNotIn('"maxLength"', wire_schema)
            # Local validation retains the full constraints, regardless of transport support.
            self.assertFalse(NoteResponse.model_json_schema()["additionalProperties"])
            self.assertTrue(any("inlineData" in p for c in request["contents"] for p in c["parts"]))
        finally:
            client.close()

    def test_truncated_response_is_retried_and_not_accepted(self):
        calls = []

        def handler(request):
            calls.append(request)
            return self.response('{"blocks": []}', finish="MAX_TOKENS")

        client = self.client(handler)
        try:
            with patch("interpretation.gemini_adapter.time.sleep"):
                with self.assertRaises(ValueError):
                    GeminiAdapter(Settings(retries=1), client=client).generate(NoteResponse, "Test", {})
            self.assertEqual(len(calls), 2)
        finally:
            client.close()

    def test_permission_error_is_not_retried(self):
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(403, json={"error": {"code": 403, "message": "Fixture denied", "status": "PERMISSION_DENIED"}})

        client = self.client(handler)
        try:
            with self.assertRaises(Exception):
                GeminiAdapter(Settings(retries=2), client=client).generate(NoteResponse, "Test", {})
            self.assertEqual(len(calls), 1)
        finally:
            client.close()
