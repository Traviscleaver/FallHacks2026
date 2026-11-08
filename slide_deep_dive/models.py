"""Structured output schema for the AI concept breakdown."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Concept(BaseModel):
    term: str = Field(description="Short name of the concept.")
    explanation: str = Field(description="One or two plain-language sentences.")


class Formula(BaseModel):
    expression: str = Field(description="The formula in plain-text/Unicode math, e.g. z = W·x + b.")
    meaning: str = Field(description="What each symbol means and what the formula does.")


class ConceptBreakdown(BaseModel):
    summary: str = Field(description="Two or three sentences: what this slide is about.")
    key_concepts: list[Concept] = Field(description="The 2-6 most important ideas on the slide.")
    formulas: list[Formula] = Field(description="Formulas on the slide; empty if none.")
    lecture_connection: str = Field(
        description=(
            "What the professor said about this slide according to the transcript. "
            "Empty string if the transcript does not discuss it."
        )
    )
    check_questions: list[str] = Field(
        description="1-3 short questions a student could answer to check understanding."
    )
