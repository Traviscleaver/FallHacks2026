"""Stored offsets are Python code points; pages are one-based PDF pages."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Segment(Record):
    id: str
    lecture_id: str
    sequence: int = Field(ge=0)
    text: str
    text_revision: int = Field(default=1, ge=1)
    audio_range: tuple[int, int] | None = None
    timing_quality: str = "approximate"

    @model_validator(mode="after")
    def valid_audio(self):
        if self.audio_range and not 0 <= self.audio_range[0] <= self.audio_range[1]:
            raise ValueError("Invalid sample range")
        return self

    @classmethod
    def from_recording(cls, value):
        return cls(**{k: getattr(value, k) for k in cls.model_fields if hasattr(value, k)})


class Span(Record):
    segment_id: str
    text_revision: int = Field(ge=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)


class Boundary(Record):
    segment_id: str
    text_revision: int = Field(ge=1)
    offset: int = Field(ge=0)


class Formula(Record):
    slide_id: str
    latex: str = Field(default="", max_length=4000)
    plain_text: str = Field(max_length=4000)
    symbols: list[str] = Field(default_factory=list, max_length=100)
    conditions: str = Field(default="", max_length=4000)
    uncertain: bool = False


class Visual(Record):
    slide_id: str
    kind: Literal["diagram", "chart", "table", "image", "other"]
    description: str = Field(max_length=6000)
    labels: list[str] = Field(default_factory=list, max_length=100)


class PageMetadata(Record):
    slide_id: str
    title: str = Field(default="", max_length=500)
    printed_slide_number: str | None = None
    uncertain: bool = False


class SubsectionProposal(Record):
    title: str = Field(max_length=500)
    summary: str = Field(max_length=6000)
    slide_ids: list[str] = Field(min_length=1, max_length=100)
    key_terms: list[str] = Field(default_factory=list, max_length=100)
    definitions: list[str] = Field(default_factory=list, max_length=100)
    formulas: list[Formula] = Field(default_factory=list, max_length=100)
    visuals: list[Visual] = Field(default_factory=list, max_length=100)


class SlideExtraction(Record):
    pages: list[PageMetadata]
    subsections: list[SubsectionProposal]


class Slide(Record):
    id: str
    page_number: int = Field(ge=1)
    extracted_text: str = ""
    page_image_asset_id: str | None = None
    title: str = ""
    printed_slide_number: str | None = None
    status: Literal["pending", "ready", "failed"] = "pending"
    uncertain: bool = False
    error: str | None = None


class Subsection(SubsectionProposal):
    id: str
    search_text: str


class SlideIndex(Record):
    version: str
    pdf_hash: str
    status: Literal["pending", "ready", "partial", "failed"] = "pending"
    slides: list[Slide]
    subsections: list[Subsection] = Field(default_factory=list)
    glossary: list[str] = Field(default_factory=list)


class Analysis(Record):
    through_segment_id: str
    transition: Literal["continue", "candidate", "confirm", "uncertain"]
    boundary: Boundary | None = None
    title: str = Field(default="Unmatched discussion", max_length=500)
    next_title: str = Field(default="Unmatched discussion", max_length=500)
    subsection_ids: list[str] = Field(default_factory=list)
    primary_slide_id: str | None = None
    match_status: Literal["matched", "uncertain", "off_slide"]
    strength: Literal["weak", "moderate", "strong"]
    rationale: str = Field(max_length=2000)
    supporting_spans: list[Span]


class Section(Record):
    id: str
    title: str
    status: Literal["open", "closing", "closed"] = "open"
    revision: int = 1
    spans: list[Span] = Field(default_factory=list)
    subsection_ids: list[str] = Field(default_factory=list)
    related_earlier_section_ids: list[str] = Field(default_factory=list)
    pending_boundary: Boundary | None = None
    pending_title: str | None = None
    close_reason: str | None = None


class NoteProposal(Record):
    markdown: str = Field(min_length=1, max_length=16000)
    source_spans: list[Span] = Field(default_factory=list)
    slide_ids: list[str] = Field(default_factory=list)
    origin: Literal["lecture", "slide_background"]


class NoteResponse(Record):
    blocks: list[NoteProposal] = Field(min_length=1, max_length=100)


class NoteBlock(NoteProposal):
    id: str
    section_id: str
    version: int
    status: Literal["provisional", "section_final"]
    generation_key: str
    user_edited: bool = False
    stale: bool = False
    coverage_warnings: list[str] = Field(default_factory=list)


class Alignment(Record):
    source_spans: list[Span]
    decision: Analysis
    accepted_primary_slide_id: str | None
    index_version: str | None
    applied_transition: Literal["continue", "candidate", "confirm", "uncertain"] | None = None
    boundary_warning: str | None = None


class LectureState(Record):
    lecture_id: str
    revision: int = 0
    segments: list[Segment] = Field(default_factory=list)
    index: SlideIndex | None = None
    sections: list[Section] = Field(default_factory=list)
    notes: list[NoteBlock] = Field(default_factory=list)
    alignments: list[Alignment] = Field(default_factory=list)
    processed_sequence: int = -1
    gaps: list[tuple[int, int]] = Field(default_factory=list)
    transcript_complete: bool = False
    finalized: bool = False
    processing_error: str | None = None


def validate_span(span: Span, segments: dict[str, Segment]):
    segment = segments.get(span.segment_id)
    if segment is None or span.text_revision != segment.text_revision:
        raise ValueError("Unknown segment or stale text revision")
    if not 0 <= span.start_offset < span.end_offset <= len(segment.text):
        raise ValueError("Invalid source offsets")
