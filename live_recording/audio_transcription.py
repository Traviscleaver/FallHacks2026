"""Transcription types and interface for Lecture Buddy (Person B).

The concrete Gemini adapter lives behind ``Transcriber``.
``LiveRecordingManager`` uses only this interface.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence

from .audio_recording import SAMPLE_RATE, AudioPacket


@dataclass(frozen=True)
class TranscriptResult:
    """A normalized message produced by a ``Transcriber`` implementation."""

    text: str
    is_final: bool
    # Optional model-supplied timing, in samples on the capture timeline.
    audio_range: Optional[tuple[int, int]] = None


class Transcriber(Protocol):
    """Gemini transcription adapter. Implemented separately.

    Results and asynchronous errors can arrive on any thread through the
    callbacks passed to ``connect``.
    """

    def connect(
        self,
        on_result: Callable[[TranscriptResult], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        """Open the session. Raise if the connection cannot be established."""

    def send_batch(self, packets: Sequence[AudioPacket]) -> None:
        """Send a batch of consecutive packets. Raise on connection failure."""

    def finish(self, timeout: float) -> None:
        """Signal end of stream and block until pending final results have
        been delivered through ``on_result``, or ``timeout`` seconds pass."""

    def close(self) -> None:
        """Tear down the connection. Must be safe to call more than once."""


# --------------------------------------------------------------------------- #
# Gemini Live adapter
# --------------------------------------------------------------------------- #

DEFAULT_MODEL = "gemini-3.5-transcribe-live"
_AUDIO_MIME = f"audio/pcm;rate={SAMPLE_RATE}"


class _LiveSession:
    """One Gemini Live connection plus its receive task."""

    def __init__(self) -> None:
        self.stack = contextlib.AsyncExitStack()
        self.session = None
        self.receiver: Optional[asyncio.Task] = None
        self.opened_at = time.monotonic()
        self.audio_since_final = False  # audio sent that has no final text yet
        self.ending = False  # audio_stream_end has been sent
        self.closing = False  # we are tearing it down on purpose
        self.drained = asyncio.Event()  # finals for all sent audio delivered


class GeminiTranscriber:
    """``Transcriber`` backed by the Gemini Live API (``google-genai`` SDK).

    The SDK is asyncio-based, so this class runs its own event loop on a
    background thread and the blocking ``Transcriber`` methods hand work to
    it. Live transcription sessions are capped at 10 minutes, so the adapter
    rotates to a fresh session before that (or when the server sends
    ``go_away``). The old session is drained in the background, so no audio
    is dropped and the manager never sees a disconnect.

    The API key is read from the ``GEMINI_KEY`` environment variable.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        language_codes: Sequence[str] = (),
        custom_vocabulary: Sequence[str] = (),
        mode: Optional[str] = None,  # "VERBATIM" (server default) or "SMART"
        connect_timeout: float = 10.0,
        send_timeout: float = 5.0,
        rotate_after: float = 9 * 60.0,
        api_key: Optional[str] = None,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_KEY")
        if not key:
            raise RuntimeError("GEMINI_KEY environment variable is not set")

        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(api_key=key)
        self._model = model
        # Even an empty language_codes list is rejected by the installed SDK's
        # Gemini Live serializer. Omit optional hints unless explicitly supplied.
        transcription = {}
        if language_codes:
            transcription["language_codes"] = list(language_codes)
        if custom_vocabulary:
            transcription["custom_vocabulary"] = list(custom_vocabulary)
        if mode:
            transcription["mode"] = mode
        self._config = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=types.AudioTranscriptionConfig(**transcription),
        )
        self._connect_timeout = connect_timeout
        self._send_timeout = send_timeout
        self._rotate_after = rotate_after

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._on_result: Callable[[TranscriptResult], None] = lambda _r: None
        self._on_error: Callable[[Exception], None] = lambda _e: None

        # The fields below are touched only on the event loop thread.
        self._current: Optional[_LiveSession] = None
        self._retiring: set[asyncio.Task] = set()
        self._rotation: Optional[asyncio.Task] = None
        self._failure: Optional[Exception] = None

    # ------------------------------------------------------------------ API

    def connect(
        self,
        on_result: Callable[[TranscriptResult], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        # The manager may reconnect after an async error without calling
        # close() first, so always start from a clean slate.
        self.close()
        self._on_result = on_result
        self._on_error = on_error
        self._start_loop()
        try:
            self._call(self._open_current(), self._connect_timeout)
        except BaseException:
            self.close()
            raise

    def send_batch(self, packets: Sequence[AudioPacket]) -> None:
        if not packets:
            return
        pcm = b"".join(p.pcm_bytes for p in packets)
        self._call(self._send(pcm), self._send_timeout)

    def finish(self, timeout: float) -> None:
        if self._loop is None:
            return
        try:
            self._call(self._finish(timeout), timeout + 1.0)
        except concurrent.futures.TimeoutError as exc:
            raise TimeoutError("Final transcription did not finish before the timeout") from exc

    def close(self) -> None:
        loop, thread = self._loop, self._thread
        if loop is None:
            return
        try:
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(self._close_all(), loop).result(5.0)
        except Exception:  # noqa: BLE001 - best effort teardown
            pass
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(5.0)
            if not loop.is_running():
                loop.close()
            self._loop = self._thread = None
            # Anything left here belongs to the dead loop.
            self._current = None
            self._rotation = None
            self._retiring = set()

    # ------------------------------------------------------- loop plumbing

    def _start_loop(self) -> None:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=loop.run_forever, name="gemini-transcriber", daemon=True
        )
        thread.start()
        self._loop, self._thread = loop, thread

    def _call(self, coro, timeout: float):
        if self._loop is None:
            coro.close()
            raise ConnectionError("Transcriber is not connected")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # ------------------------------------------------- coroutines (loop thread)

    async def _open_session(self) -> _LiveSession:
        sess = _LiveSession()
        try:
            sess.session = await sess.stack.enter_async_context(
                self._client.aio.live.connect(model=self._model, config=self._config)
            )
        except BaseException:
            await sess.stack.aclose()
            raise
        sess.receiver = asyncio.create_task(self._receive(sess))
        return sess

    async def _open_current(self) -> None:
        self._failure = None
        self._current = await self._open_session()

    async def _send(self, pcm: bytes) -> None:
        if self._failure is not None:
            raise ConnectionError(f"Gemini session failed: {self._failure}")
        sess = self._current
        if sess is None:
            raise ConnectionError("Transcriber is not connected")

        await sess.session.send_realtime_input(
            audio=self._types.Blob(data=pcm, mime_type=_AUDIO_MIME)
        )
        sess.audio_since_final = True

        if time.monotonic() - sess.opened_at >= self._rotate_after:
            self._request_rotation()

    async def _finish(self, timeout: float) -> None:
        if self._rotation is not None:
            _, pending = await asyncio.wait({self._rotation}, timeout=timeout)
            if pending:
                raise TimeoutError("Transcription session rotation did not finish")
        sess = self._current
        waits = set(self._retiring)
        if sess is not None and sess.audio_since_final and self._failure is None:
            await self._end_stream(sess)
            waits.add(asyncio.ensure_future(sess.drained.wait()))
        if waits:
            _, pending = await asyncio.wait(waits, timeout=timeout)
            if pending:
                for task in pending - self._retiring:
                    task.cancel()
                await asyncio.gather(*(pending - self._retiring), return_exceptions=True)
                raise TimeoutError("Final transcription did not finish before the timeout")
        if self._failure is not None:
            raise ConnectionError("Transcription ended after a connection failure")

    async def _close_all(self) -> None:
        tasks = [t for t in (self._rotation, *self._retiring) if t is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._rotation, self._retiring = None, set()
        sess, self._current = self._current, None
        if sess is not None:
            await self._close_session(sess)

    # Session rotation (10-minute limit / go_away).

    def _request_rotation(self) -> None:
        if self._rotation is None or self._rotation.done():
            self._rotation = asyncio.create_task(self._rotate())

    async def _rotate(self) -> None:
        try:
            new = await self._open_session()
        except Exception as exc:  # noqa: BLE001
            self._report_failure(exc)
            return
        old, self._current = self._current, new
        if old is not None:
            task = asyncio.create_task(self._retire(old))
            self._retiring.add(task)
            task.add_done_callback(self._retiring.discard)

    async def _retire(self, sess: _LiveSession) -> None:
        try:
            if sess.audio_since_final:
                await self._end_stream(sess)
                await asyncio.wait_for(sess.drained.wait(), self._connect_timeout)
        except Exception:  # noqa: BLE001 - its audio is already covered
            pass
        finally:
            await self._close_session(sess)

    async def _end_stream(self, sess: _LiveSession) -> None:
        sess.ending = True
        await sess.session.send_realtime_input(audio_stream_end=True)

    async def _close_session(self, sess: _LiveSession) -> None:
        sess.closing = True
        sess.drained.set()
        if sess.receiver is not None and sess.receiver is not asyncio.current_task():
            sess.receiver.cancel()
            await asyncio.gather(sess.receiver, return_exceptions=True)
        try:
            await sess.stack.aclose()
        except Exception:  # noqa: BLE001
            pass

    # Receiving.

    async def _receive(self, sess: _LiveSession) -> None:
        try:
            while not sess.closing:
                got_any = False
                # receive() yields the messages of one turn, then returns.
                async for message in sess.session.receive():
                    got_any = True
                    self._handle(sess, message)
                if not got_any and not sess.closing:
                    raise ConnectionError("Gemini Live connection closed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not sess.closing and sess is self._current:
                self._report_failure(exc)
        finally:
            sess.drained.set()

    def _handle(self, sess: _LiveSession, message) -> None:
        if getattr(message, "go_away", None) is not None and sess is self._current:
            self._request_rotation()

        content = getattr(message, "server_content", None)
        if content is None:
            return

        interim = getattr(content, "interim_input_transcription", None)
        if interim is not None and interim.text:
            self._deliver(TranscriptResult(text=interim.text, is_final=False))

        final = getattr(content, "input_transcription", None)
        if final is not None and final.text and final.text.strip():
            sess.audio_since_final = False
            self._deliver(TranscriptResult(text=final.text.strip(), is_final=True))
            if sess.ending:
                sess.drained.set()

        if sess.ending and getattr(content, "turn_complete", False):
            sess.drained.set()

    def _deliver(self, result: TranscriptResult) -> None:
        try:
            self._on_result(result)
        except Exception:  # noqa: BLE001 - never let a UI bug kill the receiver
            pass

    def _report_failure(self, exc: Exception) -> None:
        if self._failure is not None:
            return
        self._failure = exc
        try:
            self._on_error(exc)
        except Exception:  # noqa: BLE001
            pass
