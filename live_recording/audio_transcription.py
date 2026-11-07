"""Transcription types and interface for Lecture Buddy (Person B).

The concrete Gemini adapter lives behind ``Transcriber``.
``LiveRecordingManager`` uses only this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, Sequence

from .audio_recording import AudioPacket


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
