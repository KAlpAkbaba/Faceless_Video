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
    """One generated clip."""

    beat_label: str = Field(description="Short label for the story beat this illustrates.")
    characters: list[str] = Field(
        description=(
            "Which of the channel's characters appear in this shot, by name, most "
            "important first. Empty for a shot with no characters in it."
        )
    )
    prompt: str = Field(
        description=(
            "Self-contained text-to-video prompt: subject, setting, lighting, camera move. "
            "No text or captions in frame. No named real people."
        )
    )


class ScriptLine(BaseModel):
    """One spoken line, and who says it."""

    speaker: str = Field(
        description=(
            "Which character says this line, by name. Use 'Narrator' only for the "
            "rare line no character could say."
        )
    )
    text: str = Field(
        description=(
            "What is said, as it should be spoken. No name prefix, no quotation "
            "marks, no stage directions, no emoji, no markdown."
        )
    )


class VideoScript(BaseModel):
    """A complete, ready-to-voice cut."""

    title: str = Field(description="Final YouTube title, under 70 characters, no clickbait lies.")
    lines: list[ScriptLine] = Field(
        description=(
            "The whole episode as dialogue, in order. The characters carry the "
            "story themselves; a narrator explaining them over the top is what "
            "preschool animation specifically does not do."
        )
    )
    shots: list[Shot] = Field(description="Visual prompts covering the story arc, in order.")
    description: str = Field(description="YouTube description. Plain text, 2-4 short paragraphs.")
    tags: list[str] = Field(description="8-15 lowercase YouTube tags.")
    thumbnail_text: str = Field(description="2-4 words for the thumbnail overlay, uppercase-friendly.")

    @property
    def narration(self) -> str:
        """Everything spoken, as one block — for captions and word counts."""
        return " ".join(line.text.strip() for line in self.lines if line.text.strip())

    @property
    def speakers(self) -> list[str]:
        return list(dict.fromkeys(line.speaker for line in self.lines))


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
