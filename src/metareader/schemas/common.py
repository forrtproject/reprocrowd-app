"""Small types shared by several schemas."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class Model(BaseModel):
    """Base for all MetaReader schemas: unknown fields are errors, so typos don't pass silently."""

    model_config = ConfigDict(extra="forbid")


class Box(Model):
    """A rectangle on a PDF page."""

    page: int = Field(ge=1, description="1-based page number")
    x0: float
    y0: float
    x1: float
    y1: float
    origin: Literal["top", "bottom"] = Field(description='"top": y measured from the page top (GROBID); "bottom": PDF native')


IdentifierScheme = Literal["doi", "openalex", "pmid", "pmcid"]


class Identifier(Model):
    """A work identifier that OpenAlex can resolve."""

    scheme: IdentifierScheme
    value: str = Field(min_length=1, description="normalized: DOI lower-case without resolver prefix, OpenAlex id like W123, PMID digits, PMCID like PMC123")


class LlmCall(Model):
    """Everything needed to understand (and repeat) one LLM call."""

    id: str = Field(description="also the folder llm-calls/<id>/ holding prompt.txt, stdout.txt, stderr.txt")
    task: Literal["reconcile", "targets", "actions"]
    profile: str = Field(description="name of the LLM profile in the service config")
    argv: list[str] = Field(description="the exact command line that was run (prompt goes to stdin)")
    model: str = Field(description='model passed to the CLI; "" = the CLI default')
    effort: str = Field(description='effort level passed to the CLI; "" = the CLI default')
    template: Literal["default", "custom"] = Field(description="whether the built-in prompt template or a caller-supplied one was used")
    instructions: str = Field(default="", description="extra instructions appended to the template")
    prompt: str = Field(description="the full prompt sent")
    output: str = Field(description="raw stdout of the call")
    started_at: datetime
    duration_ms: int
    exit_code: int | None = None
