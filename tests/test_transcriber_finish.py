"""Regression coverage for real SDK configuration and unfinished speech at stop."""
import asyncio
from types import SimpleNamespace
import unittest

from google.genai import _live_converters
from live_recording.audio_transcription import GeminiTranscriber, _LiveSession


class TranscriberFinishTest(unittest.TestCase):
    def setUp(self):
        self.transcriber = GeminiTranscriber(api_key="fixture-key")

    def tearDown(self):
        self.transcriber._client.close()

    def test_default_config_can_be_serialized_for_gemini_live(self):
        config = self.transcriber._config.model_dump(exclude_none=True)
        result = _live_converters._LiveConnectConfig_to_mldev(self.transcriber._client._api_client, config)
        self.assertIsNotNone(result)
        self.assertNotIn("language_codes", config.get("input_audio_transcription", {}))

    def test_undelivered_final_speech_raises_timeout(self):
        async def run():
            session = _LiveSession()
            session.audio_since_final = True

            async def send(**kwargs):
                pass

            session.session = SimpleNamespace(send_realtime_input=send)
            self.transcriber._current = session
            with self.assertRaises(TimeoutError):
                await self.transcriber._finish(0.001)
        asyncio.run(run())

    def test_delivered_final_speech_finishes_successfully(self):
        async def run():
            session = _LiveSession()
            session.audio_since_final = True

            async def send(**kwargs):
                session.drained.set()

            session.session = SimpleNamespace(send_realtime_input=send)
            self.transcriber._current = session
            await self.transcriber._finish(1)
        asyncio.run(run())
