"""Slide deep-dive: AI concept breakdown + contextual Q&A for a single slide."""

from .context import DeepDiveContext, slide_context_from_pdf
from .models import ConceptBreakdown
from .service import DeepDiveService

__all__ = ["ConceptBreakdown", "DeepDiveContext", "DeepDiveService", "slide_context_from_pdf"]
