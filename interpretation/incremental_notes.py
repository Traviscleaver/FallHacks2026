"""Ordered alignment, delayed topic boundaries and source-grounded section notes."""
from hashlib import sha256
import json
import re

from .config import Settings
from .models import (Alignment, Analysis, NoteBlock, NoteResponse, Section, Segment,
                     Span, validate_span)
from .repository import StaleSnapshot
from .slide_preparation import stable_id

PROMPT_VERSION = "sections-v1"


def full_span(segment):
    return Span(segment_id=segment.id, text_revision=segment.text_revision,
                start_offset=0, end_offset=len(segment.text))


def contained(span, allowed):
    return any(span.segment_id == a.segment_id and span.text_revision == a.text_revision
               and a.start_offset <= span.start_offset < span.end_offset <= a.end_offset for a in allowed)


def split_spans(spans, boundary, segments):
    before, after = [], []
    boundary_sequence = segments[boundary.segment_id].sequence
    for span in spans:
        sequence = segments[span.segment_id].sequence
        if sequence < boundary_sequence:
            before.append(span)
        elif sequence > boundary_sequence:
            after.append(span)
        else:
            if span.start_offset < boundary.offset:
                before.append(span.model_copy(update={"end_offset": boundary.offset}))
            if boundary.offset < span.end_offset:
                after.append(span.model_copy(update={"start_offset": boundary.offset}))
    return before, after


def duration(spans, segments):
    seconds = 0.0
    for span in spans:
        s = segments[span.segment_id]
        fraction = (span.end_offset - span.start_offset) / max(1, len(s.text))
        samples = (s.audio_range[1] - s.audio_range[0]) if s.audio_range else 0
        # Used only for checkpoint scheduling, never displayed as precise spoken time.
        seconds += samples / 16000 * fraction if samples > 0 else (span.end_offset - span.start_offset) / 15
    return seconds


class IncrementalNotes:
    def __init__(self, repository, ai, settings=None):
        self.repo, self.ai = repository, ai
        self.settings = settings or Settings()

    def accept_final_segment(self, lecture_id, recording_segment):
        segment = Segment.from_recording(recording_segment)
        if segment.lecture_id != lecture_id:
            raise ValueError("Segment belongs to another lecture")
        for _ in range(8):
            state = self.repo.load(lecture_id)
            old = next((s for s in state.segments if s.id == segment.id), None)
            if old == segment:
                return  # duplicate event
            if old:
                if old.sequence != segment.sequence or segment.text_revision <= old.text_revision:
                    raise ValueError("Corrections require stable sequence and increasing text_revision")
                state.segments = [segment if s.id == old.id else s for s in state.segments]
                # Conservative replay prevents old offsets/boundaries surviving a correction.
                state.sections, state.alignments, state.processed_sequence = [], [], -1
                for note in state.notes:
                    note.stale = True
            else:
                if any(s.sequence == segment.sequence for s in state.segments):
                    raise ValueError("Duplicate sequence with a different segment ID")
                if segment.sequence <= state.processed_sequence:
                    raise ValueError("Late segment requires an explicit transcript correction/replay")
                state.segments.append(segment)
                state.segments.sort(key=lambda s: s.sequence)
            state.transcript_complete, state.finalized = False, False
            try:
                self.repo.commit(state, state.revision)
                return
            except StaleSnapshot:
                continue
        raise StaleSnapshot("Transcript changed repeatedly during ingestion")

    def record_gap(self, lecture_id, start_sample, end_sample):
        if not 0 <= start_sample < end_sample:
            raise ValueError("Invalid audio gap")
        for _ in range(8):
            state = self.repo.load(lecture_id)
            gap = (start_sample, end_sample)
            if gap in state.gaps:
                return
            state.gaps.append(gap)
            # Previous summaries remain available, but may not carry the new coverage warning.
            for note in state.notes:
                if not note.user_edited:
                    note.stale = True
            try:
                self.repo.commit(state, state.revision)
                return
            except StaleSnapshot:
                continue
        raise StaleSnapshot("Gap update conflicted repeatedly")

    def _candidates(self, state, text):
        groups = state.index.subsections if state.index else []
        # Tiny decks use the complete compact outline. Larger ones use keyword ranking,
        # plus current links. Embeddings can later replace this without changing the port.
        tokens = set(re.findall(r"\w+", text.casefold()))
        ranked = sorted(groups, key=lambda g: len(tokens & set(re.findall(r"\w+", g.search_text.casefold()))), reverse=True)
        if len(groups) > 8:
            current_ids = set(state.sections[-1].subsection_ids) if state.sections else set()
            ranked = ranked[:5] + [g for g in groups if g.id in current_ids and g not in ranked[:5]]
        result, size = [], 0
        for group in ranked:
            count = len(group.search_text)
            if size + count <= self.settings.text_budget:
                result.append(group)
                size += count
        return result

    def process_finalized_speech(self, lecture_id, through_segment_id=None):
        """Process one bounded ordered batch; caller repeats while backlog remains."""
        state = self.repo.load(lecture_id)
        cutoff = next((s.sequence for s in state.segments if s.id == through_segment_id), None)
        if through_segment_id is not None and cutoff is None:
            raise ValueError("Unknown transcript cutoff")
        new, size = [], 0
        for segment in state.segments:
            if segment.sequence <= state.processed_sequence or (cutoff is not None and segment.sequence > cutoff):
                continue
            if segment.sequence != state.processed_sequence + len(new) + 1:
                raise ValueError("Transcript sequence gap: hydrate missing saved segments before analysis")
            if len(segment.text) > self.settings.text_budget:
                raise ValueError("Single transcript segment exceeds C_TEXT_BUDGET; increase the budget")
            if new and (len(new) >= self.settings.batch_segments or size + len(segment.text) > self.settings.text_budget):
                break
            new.append(segment)
            size += len(segment.text)
        if not new:
            return state
        new_spans = [full_span(s) for s in new if s.text]
        if not new_spans:
            state.processed_sequence = new[-1].sequence
            self.repo.commit(state, state.revision)
            return self.repo.load(lecture_id)
        active = state.sections[-1] if state.sections and state.sections[-1].status != "closed" else None
        by_id = {s.id: s for s in state.segments}
        preceding, remaining = [], self.settings.text_budget
        if active:
            for span in reversed(active.spans):
                if span.end_offset - span.start_offset > remaining:
                    break
                preceding.insert(0, span)
                remaining -= span.end_offset - span.start_offset
            if active.pending_boundary:
                boundary_id = active.pending_boundary.segment_id
                pending_span = next(s for s in active.spans if s.segment_id == boundary_id)
                if pending_span not in preceding:
                    preceding.insert(0, pending_span)
        supplied = preceding + new_spans
        candidates = self._candidates(state, " ".join(s.text for s in new))
        allowed_groups = {g.id for g in candidates}
        allowed_slides = {sid for g in candidates for sid in g.slide_ids}
        decision = self.ai.generate(
            Analysis,
            "Analyze all new finalized speech in order. Chunks are transport units, not topics. "
            "Definitions, formulas and further examples usually continue one discussion. "
            "Use candidate for a possible topic shift, then confirm at the SAME earlier source "
            "boundary after subsequent speech supports it. An explicit strong transition may "
            "confirm immediately. A page turn, silence or passing reference is insufficient. "
            "Return at most one boundary per batch; keep further transitions for later review. "
            "Boundary offset is a Python code-point position where the NEXT discussion begins. "
            "The first topic opening is a continuation, not a transition from an empty section. "
            "Never propose a boundary at the start of the active discussion or one leaving "
            "only whitespace on either side. "
            "Use only supplied IDs/revisions/offsets. Allow off_slide/uncertain. Matched IDs "
            "describe speech after the boundary, or the current topic if continuing. "
            "Supporting spans must be present in supplied speech; do not claim projected-page observation.",
            {"through_segment_id": new[-1].id,
             "new_segment_ids": [s.id for s in new],
             "speech": [{**span.model_dump(), "text": by_id[span.segment_id].text[span.start_offset:span.end_offset]}
                        for span in supplied],
             "active_section": active.model_dump() if active else None,
             "previous_alignment": state.alignments[-1].model_dump() if state.alignments else None,
             "candidates": [g.model_dump() for g in candidates]},
        )
        if decision.through_segment_id != new[-1].id:
            raise ValueError("Model returned a different transcript cutoff")
        if not set(decision.subsection_ids) <= allowed_groups or (decision.primary_slide_id and decision.primary_slide_id not in allowed_slides):
            raise ValueError("Model invented candidate IDs")
        if decision.primary_slide_id and not any(decision.primary_slide_id in g.slide_ids for g in candidates if g.id in decision.subsection_ids):
            raise ValueError("Primary slide must belong to a referenced subsection")
        if decision.match_status == "matched" and not decision.subsection_ids:
            raise ValueError("Matched decision requires a subsection")
        if decision.match_status == "off_slide" and (decision.subsection_ids or decision.primary_slide_id):
            raise ValueError("Off-slide decision cannot claim slide links")
        for span in decision.supporting_spans:
            validate_span(span, by_id)
            if not contained(span, supplied):
                raise ValueError("Evidence outside analysis snapshot")
        if not decision.supporting_spans:
            raise ValueError("Analysis requires supporting speech")
        boundary = decision.boundary
        if decision.transition in ("candidate", "confirm") and boundary is None:
            raise ValueError("Transition needs a source boundary")
        if boundary:
            s = by_id.get(boundary.segment_id)
            if s is None or boundary.text_revision != s.text_revision or not 0 <= boundary.offset < len(s.text):
                raise ValueError("Invalid topic boundary")
            if not any(p.segment_id == s.id and p.start_offset <= boundary.offset < p.end_offset for p in supplied):
                raise ValueError("Boundary outside supplied speech")
        raw_decision = decision.model_copy(deep=True)
        boundary_warning = None
        before, after = [], []
        if boundary and decision.transition in ("candidate", "confirm"):
            before, after = split_spans((active.spans if active else []) + new_spans, boundary, by_id)
            has_before = any(by_id[p.segment_id].text[p.start_offset:p.end_offset].strip() for p in before)
            has_after = any(by_id[p.segment_id].text[p.start_offset:p.end_offset].strip() for p in after)
            if not has_before or not has_after:
                # This is a valid source location but an unusable topic split.
                # Keep every span and continue the discussion instead of aborting ingestion.
                boundary_warning = "Ignored boundary that would create an empty discussion."
                updates = {"transition": "continue", "boundary": None}
                if active is None and not has_before and decision.next_title != "Unmatched discussion":
                    updates["title"] = decision.next_title
                decision = decision.model_copy(update=updates)
            elif (decision.transition == "confirm" and decision.strength != "strong"
                  and (active is None or active.pending_boundary != boundary)):
                # Weak evidence starts a proposal; later speech can confirm it.
                boundary_warning = "Boundary awaits confirmation from subsequent speech."
                decision = decision.model_copy(update={"transition": "candidate"})
        if active is None:
            first = new_spans[0]
            active = Section(id=stable_id(lecture_id, "section", first.segment_id, first.start_offset),
                             title=decision.title)
            state.sections.append(active)
        active.spans.extend(new_spans)
        active.revision += 1
        if decision.transition == "confirm":
            active.spans, active.status, active.close_reason = before, "closed", decision.rationale
            active.pending_boundary, active.pending_title = None, None
            new_section = Section(id=stable_id(lecture_id, "section", boundary.segment_id, boundary.offset),
                                  title=decision.next_title, spans=after,
                                  subsection_ids=decision.subsection_ids if decision.match_status == "matched" else [])
            new_section.related_earlier_section_ids = [s.id for s in state.sections
                if set(s.subsection_ids) & set(new_section.subsection_ids)]
            state.sections.append(new_section)
        else:
            if decision.transition == "candidate":
                active.status, active.pending_boundary, active.pending_title = "closing", boundary, decision.next_title
            elif decision.transition == "continue":
                active.status, active.pending_boundary, active.pending_title = "open", None, None
            if decision.match_status == "matched" and decision.transition != "candidate":
                active.subsection_ids = sorted(set(active.subsection_ids) | set(decision.subsection_ids))
        previous = state.alignments[-1] if state.alignments else None
        accepted = None
        if decision.match_status == "matched":
            if decision.strength == "strong" or (previous and previous.decision.primary_slide_id == decision.primary_slide_id
                                                  and previous.decision.match_status == "matched"):
                accepted = decision.primary_slide_id
            elif previous and previous.accepted_primary_slide_id == decision.primary_slide_id:
                accepted = decision.primary_slide_id
        state.alignments.append(Alignment(source_spans=new_spans, decision=raw_decision,
            applied_transition=decision.transition, boundary_warning=boundary_warning,
            accepted_primary_slide_id=accepted, index_version=state.index.version if state.index else None))
        state.processed_sequence = new[-1].sequence
        state.processing_error = None
        # Atomic cursor + relationships; late responses cannot overwrite newer speech/corrections.
        self.repo.commit(state, state.revision)
        return self.repo.load(lecture_id)

    def generate_section_notes(self, lecture_id, section_id, expected_revision=None):
        state = self.repo.load(lecture_id)
        section = next(s for s in state.sections if s.id == section_id)
        if expected_revision is not None and section.revision != expected_revision:
            raise StaleSnapshot("Section has changed")
        by_id = {s.id: s for s in state.segments}
        for span in section.spans:
            validate_span(span, by_id)
        groups = [g for g in state.index.subsections if g.id in section.subsection_ids] if state.index else []
        slide_ids = {sid for g in groups for sid in g.slide_ids}
        status = "section_final" if section.status == "closed" else "provisional"
        key = sha256(json.dumps([section.model_dump(), [by_id[s.segment_id].model_dump() for s in section.spans],
            state.index.version if state.index else None, state.gaps, state.transcript_complete,
            PROMPT_VERSION, self.settings.model, status], sort_keys=True).encode()).hexdigest()
        existing = [n for n in state.notes if n.section_id == section_id]
        if any(n.generation_key == key and not n.stale for n in existing):
            return [n for n in existing if n.generation_key == key]
        # Long sections are generated from bounded chunks of original evidence, never recursively summarized.
        chunks, chunk, size = [], [], 0
        for span in section.spans:
            offset = span.start_offset
            while offset < span.end_offset:
                end = min(span.end_offset, offset + self.settings.text_budget - size)
                part = span.model_copy(update={"start_offset": offset, "end_offset": end})
                chunk.append(part)
                size += end - offset
                offset = end
                if size == self.settings.text_budget:
                    chunks.append(chunk)
                    chunk, size = [], 0
        if chunk:
            chunks.append(chunk)
        blocks = []
        for chunk in chunks:
            terms = set(re.findall(r"\w+", " ".join(
                by_id[p.segment_id].text[p.start_offset:p.end_offset] for p in chunk).casefold()))
            ranked_groups = sorted(groups, key=lambda g: len(terms & set(re.findall(r"\w+", g.search_text.casefold()))), reverse=True)
            supplied_groups, slide_size = [], 0
            for group in ranked_groups:
                encoded_size = len(group.model_dump_json())
                if slide_size + encoded_size <= self.settings.text_budget:
                    supplied_groups.append(group)
                    slide_size += encoded_size
            supplied_slide_ids = {sid for group in supplied_groups for sid in group.slide_ids}
            result = self.ai.generate(
                NoteResponse,
                "Write short scan-friendly notes from original finalized speech and linked slides. "
                "Preserve definitions, formulas, symbols, units, conditions, professor qualifications "
                "and examples actually given. Never invent examples or reconstruct missing audio. "
                "Distinguish lecture-supported blocks from slide_background. Cite exact supplied "
                "source spans and/or slide IDs. Include readable formula source/plain-text fallback. "
                "If gaps exist, acknowledge unavailable speech. No raw HTML or external images.",
                {"section_title": section.title, "status": status,
                 "speech": [{**p.model_dump(), "text": by_id[p.segment_id].text[p.start_offset:p.end_offset]}
                            for p in chunk],
                 "slides": [g.model_dump() for g in supplied_groups],
                 "omitted_slide_ids": sorted(slide_ids - supplied_slide_ids),
                 "audio_gaps_samples": state.gaps, "transcript_complete": state.transcript_complete},
            )
            for block in result.blocks:
                for span in block.source_spans:
                    validate_span(span, by_id)
                    if not contained(span, chunk):
                        raise ValueError("Note cites speech outside its generation snapshot")
                if not set(block.slide_ids) <= supplied_slide_ids:
                    raise ValueError("Note cites a slide outside its generation snapshot")
                if block.origin == "lecture" and not block.source_spans:
                    raise ValueError("Lecture claims require transcript evidence")
                if block.origin == "slide_background" and (not block.slide_ids or block.source_spans):
                    raise ValueError("Slide background requires slide-only evidence")
                if re.search(r"<\s*/?\s*[a-zA-Z][^>]*>|!\[|\]\(\s*(?:https?:|file:|data:)", block.markdown):
                    raise ValueError("Unsupported raw HTML or external resource in notes")
                if block not in blocks:
                    blocks.append(block)
        version = max((n.version for n in existing), default=0) + 1
        warnings = []
        if state.gaps:
            warnings.append("Some audio was not transcribed; notes cannot reconstruct missing speech.")
        if state.finalized and not state.transcript_complete:
            warnings.append("Transcription ended with incomplete coverage; these are partial notes.")
        notes = [NoteBlock(**b.model_dump(), id=stable_id(section_id, key, i), section_id=section_id,
                           version=version, status=status, generation_key=key,
                           coverage_warnings=warnings) for i, b in enumerate(blocks)]
        for old in state.notes:
            if old.section_id == section_id and not old.user_edited:
                old.stale = True
        state.notes.extend(notes)
        self.repo.commit(state, state.revision)
        return notes

    def generate_due_notes(self, lecture_id):
        state = self.repo.load(lecture_id)
        by_id = {s.id: s for s in state.segments}
        for section in state.sections:
            latest = [n for n in state.notes if n.section_id == section.id and not n.stale and not n.user_edited]
            if section.status == "closed":
                self.generate_section_notes(lecture_id, section.id, section.revision)
            else:
                old_spans = []
                for n in latest:
                    for span in n.source_spans:
                        if span not in old_spans:
                            old_spans.append(span)
                if duration(section.spans, by_id) - duration(old_spans, by_id) >= self.settings.provisional_seconds:
                    self.generate_section_notes(lecture_id, section.id, section.revision)

    def finish_lecture(self, lecture_id, *, transcript_complete: bool):
        """Call ONLY after B confirms final speech is saved; STOPPED alone is insufficient."""
        while True:
            state = self.repo.load(lecture_id)
            if not any(s.sequence > state.processed_sequence for s in state.segments):
                break
            self.process_finalized_speech(lecture_id)
        if not state.finalized or state.transcript_complete != transcript_complete:
            for section in state.sections:
                if section.status != "closed":
                    section.status, section.close_reason = "closed", "recording_ended"
                    section.pending_boundary, section.pending_title = None, None
                    section.revision += 1
            state.transcript_complete, state.finalized = transcript_complete, True
            self.repo.commit(state, state.revision)
        self.generate_due_notes(lecture_id)
        return self.repo.load(lecture_id)
