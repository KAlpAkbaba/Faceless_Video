"""Data models: Claude structured-output schemas plus internal render types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Claude structured outputs.
# Every field is required and non-defaulted — structured outputs need a strict
# schema, and a defaulted field would let the model silently skip it.
# ---------------------------------------------------------------------------


class TopicIdea(BaseModel):
    """One video concept, chosen by the ideation step."""

    working_title: str = Field(description="Plain working title, not the final YouTube title.")
    slug: str = Field(description="lowercase-hyphenated identifier, max 60 chars, ascii only")
    angle: str = Field(description="The specific argument or through-line, one sentence.")
    hook_promise: str = Field(description="What the viewer learns by the end, one sentence.")
    why_it_travels: str = Field(description="Why this works for a global English-speaking audience.")
    key_facts: list[str] = Field(description="3-6 concrete, checkable facts the script must contain.")
    fact_risk: str = Field(description="Where this topic is easy to get wrong, or 'none'.")


class Shot(BaseModel):
    """One generated B-roll clip."""

    beat_label: str = Field(description="Short label for the story beat this illustrates.")
    prompt: str = Field(
        description=(
            "Self-contained text-to-video prompt: subject, setting, lighting, camera move. "
            "No text or captions in frame. No named real people."
        )
    )


class VideoScript(BaseModel):
    """A complete, ready-to-narrate cut."""

    title: str = Field(description="Final YouTube title, under 70 characters, no clickbait lies.")
    narration: str = Field(
        description=(
            "The full spoken script as plain prose. No headings, no speaker labels, "
            "no stage directions, no emoji, no markdown."
        )
    )
    shots: list[Shot] = Field(description="Visual prompts covering the story arc, in order.")
    description: str = Field(description="YouTube description. Plain text, 2-4 short paragraphs.")
    tags: list[str] = Field(description="8-15 lowercase YouTube tags.")
    thumbnail_text: str = Field(description="2-4 words for the thumbnail overlay, uppercase-friendly.")


class ScriptPackage(BaseModel):
    """Long-form cut and its Shorts companion, written together from one topic."""

    longform: VideoScript
    shorts: VideoScript


# ---------------------------------------------------------------------------
# Internal render types.
# ---------------------------------------------------------------------------


@dataclass
class WordTiming:
    """One spoken word with its position in the narration audio."""

    word: str
    start: float
    end: float


@dataclass
class Voiceover:
    audio_path: Path
    duration: float
    words: list[WordTiming] = field(default_factory=list)


@dataclass
class ClipAsset:
    """A rendered B-roll clip on disk."""

    index: int
    path: Path
    prompt: str
    seconds: float


@dataclass
class RenderRequest:
    """Everything the assembler needs for one output file."""

    kind: str  # "longform" | "shorts"
    script: VideoScript
    voiceover: Voiceover
    clips: list[ClipAsset]
    width: int
    height: int
    burn_captions: bool
    output_path: Path


@dataclass
class RenderResult:
    kind: str
    video_path: Path
    thumbnail_path: Path | None
    duration: float
    script: VideoScript
