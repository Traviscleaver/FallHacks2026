"""Visual indexing using A's immutable, page-aligned PDF extraction handoff."""
from dataclasses import dataclass
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from .config import Settings
from .gemini_adapter import safe_error_message
from .models import Slide, SlideExtraction, SlideIndex, Subsection
from .repository import StaleSnapshot

INDEX_VERSION = "visual-v1"


def stable_id(*parts):
    return str(uuid5(NAMESPACE_URL, ":".join(map(str, parts))))


@dataclass(frozen=True)
class PageInput:
    page_number: int
    text: str
    image_png: bytes
    image_asset_id: str | None = None


@dataclass(frozen=True)
class PDFInput:
    # A/B retain the authoritative local PDF; images are temporary model inputs.
    pdf_hash: str
    pages: tuple[PageInput, ...]


def searchable(group):
    return "\n".join([group.title, group.summary, *group.key_terms, *group.definitions,
                      *[" ".join([f.plain_text, f.latex, *f.symbols, f.conditions]) for f in group.formulas],
                      *[" ".join([v.description, *v.labels]) for v in group.visuals]])


class SlidePreparation:
    def __init__(self, repository, ai, settings=None):
        self.repo, self.ai = repository, ai
        self.settings = settings or Settings()

    def _save_index(self, lecture_id, index):
        for _ in range(8):
            current = self.repo.load(lecture_id)
            if current.index and current.index.version != index.version:
                raise StaleSnapshot("A different PDF/index replaced this preparation job")
            current.index = index.model_copy(deep=True)
            try:
                self.repo.commit(current, current.revision)
                return
            except StaleSnapshot:
                continue
        raise StaleSnapshot("Storage remained busy; retry preparation")

    def prepare_slides(self, lecture_id: str, source: PDFInput, progress=None):
        if not source.pages or [p.page_number for p in source.pages] != list(range(1, len(source.pages) + 1)):
            raise ValueError("Supply every actual PDF page in one-based order")
        if len(source.pdf_hash) != 64 or any(c not in "0123456789abcdef" for c in source.pdf_hash):
            raise ValueError("A/B must provide the SHA-256 of the authoritative PDF")
        if any(not p.image_png.startswith(b"\x89PNG\r\n\x1a\n") for p in source.pages):
            raise ValueError("Every page needs original visual input, including scanned pages")
        version = sha256(f"{source.pdf_hash}:{INDEX_VERSION}:{self.settings.model}".encode()).hexdigest()
        state = self.repo.load(lecture_id)
        if state.index and state.index.version == version:
            index = state.index
            if index.status == "ready":
                return index
        else:
            index = SlideIndex(version=version, pdf_hash=source.pdf_hash, slides=[
                Slide(id=stable_id(lecture_id, source.pdf_hash, p.page_number),
                      page_number=p.page_number, extracted_text=p.text,
                      page_image_asset_id=p.image_asset_id, title=f"Page {p.page_number}")
                for p in source.pages])
            state.index = index
            # New source/index invalidates relationships, never the original transcript or edited notes.
            state.sections = []
            state.alignments = []
            state.processed_sequence = -1
            state.finalized = False
            for note in state.notes:
                note.stale = True
            self.repo.commit(state, state.revision)
        batches, batch, byte_count = [], [], 0
        for page in source.pages:
            size = len(page.image_png) + len(page.text.encode())
            if size > self.settings.slide_batch_bytes:
                raise ValueError(f"Page {page.page_number} exceeds visual batch budget; render it smaller")
            if batch and (len(batch) >= self.settings.slide_batch_pages or
                          byte_count + size > self.settings.slide_batch_bytes):
                batches.append(batch)
                # One-page overlap when it fits; proposals sharing title+pages are reconciled below.
                last = batch[-1]
                overlap_size = len(last.image_png) + len(last.text.encode())
                batch = [last] if self.settings.slide_batch_pages > 1 and overlap_size + size <= self.settings.slide_batch_bytes else []
                byte_count = overlap_size if batch else 0
            batch.append(page)
            byte_count += size
        if batch:
            batches.append(batch)
        for batch in batches:
            slides = [index.slides[p.page_number - 1] for p in batch]
            if all(s.status == "ready" for s in slides):
                continue
            try:
                result = self.ai.generate(
                    SlideExtraction,
                    "Index the page images in supplied order. Use supplied slide IDs exactly. "
                    "Return every supplied page exactly once. Extract factual content first. "
                    "Group coherent concepts across pages; multiple concepts can share a page. "
                    "Preserve formulas, symbol meanings, units, assumptions and diagram labels. "
                    "Mark unreadable/ambiguous content uncertain; do not silently fix formulas. "
                    "Use an empty title if none is readable. Printed slide labels differ from PDF order.",
                    {"pages": [{"slide_id": s.id, "pdf_page_number": s.page_number,
                                "extracted_text": s.extracted_text} for s in slides]},
                    tuple(p.image_png for p in batch),
                )
                allowed = {s.id for s in slides}
                if len(result.pages) != len(slides) or {p.slide_id for p in result.pages} != allowed:
                    raise ValueError("Model omitted, duplicated or invented PDF pages")
                covered = set()
                for group in result.subsections:
                    ids = set(group.slide_ids)
                    if not ids <= allowed or len(ids) != len(group.slide_ids):
                        raise ValueError("Invalid subsection-page references")
                    if any(item.slide_id not in ids for item in [*group.formulas, *group.visuals]):
                        raise ValueError("Formula/visual references a page outside its subsection")
                    covered.update(ids)
                if covered != allowed:
                    raise ValueError("Every PDF page needs a subsection, even if unreadable")
                by_id = {p.slide_id: p for p in result.pages}
                for slide in slides:
                    meta = by_id[slide.id]
                    slide.title = meta.title.strip() or f"Page {slide.page_number}"
                    slide.printed_slide_number = meta.printed_slide_number
                    slide.uncertain = meta.uncertain
                    slide.status, slide.error = "ready", None
                for group in result.subsections:
                    # Join a cross-batch concept only on overlapping source pages AND equal title.
                    existing = next((g for g in index.subsections
                                     if g.title.casefold() == group.title.casefold()
                                     and set(g.slide_ids) & set(group.slide_ids)), None)
                    if existing:
                        for field in ("slide_ids", "key_terms", "definitions", "formulas", "visuals"):
                            values = getattr(existing, field)
                            for value in getattr(group, field):
                                if value not in values:
                                    values.append(value)
                        existing.search_text = searchable(existing)
                    else:
                        ident = stable_id(lecture_id, version, group.title.casefold(), *sorted(group.slide_ids))
                        index.subsections.append(Subsection(**group.model_dump(), id=ident,
                                                            search_text=searchable(group)))
            except Exception as exc:
                for slide in slides:
                    if slide.status != "ready":
                        slide.status, slide.error = "failed", type(exc).__name__ + ": " + safe_error_message(exc)
            ready = sum(s.status == "ready" for s in index.slides)
            index.status = "ready" if ready == len(index.slides) else "partial" if ready else "failed"
            index.glossary = sorted({term for g in index.subsections for term in g.key_terms})
            self._save_index(lecture_id, index)
            if progress:
                progress(lecture_id, ready, len(index.slides))
        return index
