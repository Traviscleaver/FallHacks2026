"""Finalize study content for Export PDF, from original lecture evidence."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import re

from .config import Settings
from .incremental_notes import contained
from .models import FinalBlock, FinalNotes, FinalResponse, FinalSection, validate_span
from .slide_preparation import stable_id

VERSION = "final-notes-v1"


def source_key(state, model):
    # Exclude the final result and repository revision so saving the result does
    # not invalidate its own cache. Include user edits and original evidence.
    data = state.model_dump(exclude={"revision", "final_notes", "processing_error", "alignments"})
    return sha256(json.dumps([data, model, VERSION], sort_keys=True).encode()).hexdigest()


class FinalNotesService:
    def __init__(self, repository, ai, settings=None):
        self.repo, self.ai = repository, ai
        self.settings = settings or Settings()

    def finalize(self, lecture_id):
        state = self.repo.load(lecture_id)
        if not state.finalized:
            raise ValueError("Stop recording and wait for section notes before exporting")
        if not state.sections or not state.segments:
            raise ValueError("No lecture transcript is available to finalize")
        key = source_key(state, self.settings.model)
        if state.final_notes and state.final_notes.input_key == key:
            return state.final_notes
        segments = {s.id: s for s in state.segments}
        if any(s.lecture_id != lecture_id for s in segments.values()):
            raise ValueError("Transcript contains evidence from another lecture")
        notes = [n for n in state.notes if not n.stale and not n.user_edited]
        annotations = [n for n in state.notes if n.user_edited]
        groups = state.index.subsections if state.index else []
        warnings = []
        if state.gaps:
            warnings.append("Some audio was not transcribed; unavailable speech is not covered.")
        if not state.transcript_complete:
            warnings.append("Transcription is incomplete. These notes cover only available speech.")
        if state.index and state.index.status != "ready":
            warnings.append("Some slide material could not be prepared.")

        # A small lecture uses one synthesis request. Larger lectures use
        # bounded original-source chunks, with no recursive summary compression.
        units = []
        for section in state.sections:
            chunk, size = [], 0
            for span in section.spans:
                validate_span(span, segments)
                offset = span.start_offset
                while offset < span.end_offset:
                    end = min(span.end_offset, offset + self.settings.text_budget - size)
                    chunk.append(span.model_copy(update={"start_offset": offset, "end_offset": end}))
                    size += end - offset
                    offset = end
                    if size == self.settings.text_budget:
                        units.append((section, chunk))
                        chunk, size = [], 0
            if chunk:
                units.append((section, chunk))
        total_chars = sum(p.end_offset - p.start_offset for _, spans in units for p in spans)
        batches = [units] if total_chars <= self.settings.text_budget else [[unit] for unit in units]
        title, output = "Lecture notes", []
        for batch in batches:
            allowed_spans = [span for _, spans in batch for span in spans]
            section_ids = {section.id for section, _ in batch}
            group_ids = {gid for section, _ in batch for gid in section.subsection_ids}
            supplied_groups, slide_chars = [], 0
            for group in groups:
                if group.id not in group_ids:
                    continue
                length = len(group.model_dump_json())
                if slide_chars + length <= self.settings.text_budget:
                    supplied_groups.append(group)
                    slide_chars += length
            slide_ids = {sid for group in supplied_groups for sid in group.slide_ids}
            supplied_notes = [n for n in notes if n.section_id in section_ids
                and all(contained(p, allowed_spans) for p in n.source_spans)
                and set(n.slide_ids) <= slide_ids]
            if sum(len(n.markdown) for n in supplied_notes) > self.settings.text_budget:
                supplied_notes = []  # Original speech remains authoritative and complete.
            result = self.ai.generate(FinalResponse,
                "Prepare the final study notes for PDF export. Organize the supplied original "
                "speech into coherent sections; consolidate repetition and preserve definitions, "
                "formulas, readable symbol meanings, units, qualifications and worked examples. "
                "Section drafts are secondary aids, not primary evidence. Use the complete supplied "
                "speech, including the last passage. Cite exact source spans and slide IDs. Each "
                "source span should be copied from the supplied spans; do not count character positions yourself. "
                "output section must identify the supplied lecture_section_ids it covers, and all "
                "supplied sections must be represented. Distinguish lecture from slide_background; "
                "label any newly introduced teaching example additional_ai_example. Do not invent "
                "professor statements or user annotations. Do not reconstruct missing audio. "
                "Return Markdown without raw HTML, image links or external resource links. "
                "Preserve readable formula source when typesetting is unavailable.",
                {"lecture_sections": [{"id": section.id, "title": section.title,
                    "speech": [{**p.model_dump(), "text": segments[p.segment_id].text[p.start_offset:p.end_offset]}
                               for p in spans]} for section, spans in batch],
                 "slide_material": [g.model_dump() for g in supplied_groups],
                 "section_drafts": [n.model_dump() for n in supplied_notes],
                 "coverage_warnings": warnings})
            covered = set()
            for section in result.sections:
                if not set(section.lecture_section_ids) <= section_ids:
                    raise ValueError("Final notes refer to an unknown lecture section")
                covered.update(section.lecture_section_ids)
                for block in section.blocks:
                    for span in block.source_spans:
                        validate_span(span, segments)
                        if not contained(span, allowed_spans):
                            raise ValueError("Final note source is outside its request snapshot")
                    if not set(block.slide_ids) <= slide_ids:
                        raise ValueError("Final note cites an unavailable slide")
                    if block.origin == "lecture" and not block.source_spans:
                        raise ValueError("Lecture claims require transcript evidence")
                    if block.origin == "slide_background" and (not block.slide_ids or block.source_spans):
                        raise ValueError("Slide background must have slide-only evidence")
                    if block.origin == "user_annotation":
                        raise ValueError("AI must not invent user annotations")
                    if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>|!\[|\]\(\s*(?:https?:|file:|data:)", block.markdown):
                        raise ValueError("Unsupported resource or HTML in final notes")
                existing = next((s for s in output if s.title == section.title
                                 and s.lecture_section_ids == section.lecture_section_ids), None)
                if existing:
                    existing.blocks.extend(b for b in section.blocks if b not in existing.blocks)
                else:
                    output.append(section)
            if covered != section_ids:
                raise ValueError("Final notes omitted a lecture section")
            if len(batches) == 1:
                title = result.title
        # Preserve user edits verbatim; never send them to a model to be rewritten.
        for note in annotations:
            output.append(FinalSection(title="Your annotation", lecture_section_ids=[note.section_id],
                blocks=[FinalBlock(markdown=note.markdown, origin="user_annotation",
                    source_spans=note.source_spans, slide_ids=note.slide_ids)]))
        final = FinalNotes(title=title, sections=output, id=stable_id(lecture_id, key), lecture_id=lecture_id,
            version=state.final_notes.version + 1 if state.final_notes else 1,
            status="partial" if warnings else "complete", input_key=key, input_revision=state.revision,
            coverage_warnings=warnings, generated_at=datetime.now(timezone.utc).isoformat())
        state.final_notes = final
        self.repo.commit(state, state.revision)
        return final


def final_markdown(final, state):
    pages = {s.id: s.page_number for s in state.index.slides} if state.index else {}
    segments = {s.id: s.sequence + 1 for s in state.segments}
    parts = [f"# {final.title}"]
    parts.extend("> " + warning for warning in final.coverage_warnings)
    for section in final.sections:
        parts.append(f"## {section.title}")
        for block in section.blocks:
            label = {"slide_background": "Slide background", "additional_ai_example": "Additional AI example",
                     "user_annotation": "Your annotation"}.get(block.origin)
            if label:
                parts.append(f"*{label}*")
            parts.append(block.markdown)
            refs = [f"paragraph {segments[p.segment_id]}" for p in block.source_spans if p.segment_id in segments]
            refs += [f"PDF page {pages[sid]}" for sid in block.slide_ids if sid in pages]
            if refs:
                parts.append("Sources: " + ", ".join(dict.fromkeys(refs)))
    return "\n\n".join(parts)
