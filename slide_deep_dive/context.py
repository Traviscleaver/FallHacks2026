"""What the deep-dive screen is about: one slide (or a text-only section)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class DeepDiveContext:
    title: str
    badge: str
    description: str
    # PNG bytes of the rendered slide, if the context is a slide.
    slide_png: Optional[bytes] = None
    # Text extracted from the slide (helps with small fonts and formulas).
    slide_text: str = ""
    slide_number: Optional[int] = None
    total_slides: Optional[int] = None
    # Returns the finalized transcript recorded so far. Called at request
    # time, so later questions see speech that arrived after opening.
    transcript_provider: Callable[[], str] = field(default=lambda: "")


def slide_context_from_pdf(
    doc,
    slide_number: int,
    transcript_provider: Callable[[], str],
    zoom: float = 2.0,
) -> DeepDiveContext:
    """Build a context for 1-indexed ``slide_number`` of an open PyMuPDF doc."""
    import fitz

    total = len(doc)
    page = doc.load_page(slide_number - 1)
    png = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
    text = page.get_text().strip()
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    title = f"Slide {slide_number}" + (f": {first_line[:60]}" if first_line else "")
    return DeepDiveContext(
        title=title,
        badge=f"SLIDE {slide_number} OF {total}",
        description=f"Slide {slide_number} of {total} from the imported lecture deck.",
        slide_png=png,
        slide_text=text,
        slide_number=slide_number,
        total_slides=total,
        transcript_provider=transcript_provider,
    )
