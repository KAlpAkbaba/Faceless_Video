"""Topic ideation and scriptwriting, via the Claude API."""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

import anthropic

from .config import REPO_ROOT, Config, ConfigError
from .models import ScriptPackage, TopicIdea

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"
PROMPT_DIR = REPO_ROOT / "prompts"

# Spoken words per second for a documentary-paced TTS voice. Used to turn a
# target runtime in seconds into a word count for the writer.
WORDS_PER_SECOND = 2.5


def _load_prompt(name: str, **fields: str) -> str:
    path = PROMPT_DIR / name
    if not path.exists():
        raise ConfigError(f"Prompt template missing: {path}")
    text = path.read_text(encoding="utf-8")
    for key, value in fields.items():
        text = text.replace("{{" + key + "}}", value)
    leftover = re.findall(r"\{\{([A-Z_]+)\}\}", text)
    if leftover:
        raise ConfigError(f"Prompt {name} has unfilled placeholders: {sorted(set(leftover))}")
    return text


def slugify(value: str, max_length: int = 60) -> str:
    """Normalise a model-supplied slug into something safe for a filename."""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    ascii_value = re.sub(r"-{2,}", "-", ascii_value)
    return ascii_value[:max_length].strip("-") or "untitled"


def target_words(seconds: float) -> int:
    return max(40, int(round(seconds * WORDS_PER_SECOND)))


class Writer:
    """Wraps the two Claude calls that produce a day's content."""

    def __init__(self, config: Config, client: anthropic.Anthropic | None = None):
        self.config = config
        if client is not None:
            self.client = client
        else:
            api_key = config.secrets.require(
                "anthropic_api_key", "ANTHROPIC_API_KEY (Claude API key for writing scripts)"
            )
            # Scriptwriting is one slow call, not many; retry generously.
            self.client = anthropic.Anthropic(api_key=api_key, max_retries=4, timeout=600.0)

    # -- step 1: pick a topic ---------------------------------------------

    def pick_topic(self, recent_titles: list[str], avoid_slugs: set[str]) -> TopicIdea:
        history_block = "\n".join(f"- {t}" for t in recent_titles[-60:]) or "- (nothing yet)"
        banned = ", ".join(self.config.get("channel.banned_topics", [])) or "(none)"

        system = _load_prompt(
            "ideation.md",
            CHANNEL_NAME=str(self.config.get("channel.name", "the channel")),
            NICHE=str(self.config.require("channel.niche")),
            AUDIENCE=str(self.config.get("channel.audience", "a general audience")),
            TONE=str(self.config.get("channel.tone", "neutral and factual")),
            BANNED=banned,
            HISTORY=history_block,
        )

        # Up to three attempts, each one told explicitly what it just collided with.
        rejected: list[str] = []
        for attempt in range(1, 4):
            user = "Pick today's topic."
            if rejected:
                user += (
                    "\n\nThese slugs collided with topics already published — pick something"
                    " genuinely different, not a rewording: " + ", ".join(rejected)
                )
            response = self.client.messages.parse(
                model=MODEL,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=TopicIdea,
            )
            idea = response.parsed_output
            idea.slug = slugify(idea.slug or idea.working_title)
            if idea.slug not in avoid_slugs:
                log.info("Topic chosen on attempt %d: %s (%s)", attempt, idea.working_title, idea.slug)
                return idea
            log.warning("Topic %r duplicates an earlier video; retrying.", idea.slug)
            rejected.append(idea.slug)

        # Three collisions in a row means the niche is exhausted or the history
        # is polluted — surfacing that beats publishing a duplicate.
        raise RuntimeError(
            "Ideation returned an already-published topic three times in a row. "
            "Widen channel.niche or trim state/history.json."
        )

    # -- step 2: write both cuts ------------------------------------------

    def write_scripts(self, idea: TopicIdea) -> ScriptPackage:
        longform_seconds = float(self.config.get("longform.target_seconds", 360))
        shorts_seconds = float(self.config.get("shorts.target_seconds", 50))

        system = _load_prompt(
            "script.md",
            CHANNEL_NAME=str(self.config.get("channel.name", "the channel")),
            NICHE=str(self.config.require("channel.niche")),
            AUDIENCE=str(self.config.get("channel.audience", "a general audience")),
            TONE=str(self.config.get("channel.tone", "neutral and factual")),
            WORKING_TITLE=idea.working_title,
            ANGLE=idea.angle,
            HOOK_PROMISE=idea.hook_promise,
            KEY_FACTS="; ".join(idea.key_facts) or "(none supplied)",
            FACT_RISK=idea.fact_risk or "none",
            LONGFORM_WORDS=str(target_words(longform_seconds)),
            SHORTS_WORDS=str(target_words(shorts_seconds)),
            LONGFORM_SHOTS=str(int(self.config.get("video.max_clips", 12))),
            SHORTS_SHOTS=str(int(self.config.get("shorts.max_clips", 6))),
        )

        response = self.client.messages.parse(
            model=MODEL,
            max_tokens=32000,
            system=system,
            messages=[{"role": "user", "content": "Write both cuts."}],
            output_format=ScriptPackage,
        )
        package = response.parsed_output
        package.longform.narration = clean_narration(package.longform.narration)
        package.shorts.narration = clean_narration(package.shorts.narration)
        log.info(
            "Scripts written: long-form %d words / %d shots, shorts %d words / %d shots",
            len(package.longform.narration.split()),
            len(package.longform.shots),
            len(package.shorts.narration.split()),
            len(package.shorts.shots),
        )
        return package


# ---------------------------------------------------------------------------
# Narration hygiene. A stray markdown artefact is read aloud by the TTS voice,
# so strip the shapes a model occasionally emits despite the instructions.
# ---------------------------------------------------------------------------

_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_BULLET = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
_SPEAKER_LABEL = re.compile(r"^\s*(NARRATOR|VOICEOVER|VO|HOST)\s*:\s*", re.MULTILINE | re.IGNORECASE)
_STAGE_DIRECTION = re.compile(r"[\[(](?:[A-Z][A-Z\s/]{3,}|cut to|beat|pause)[^\])]*[\])]", re.IGNORECASE)
_EMPHASIS = re.compile(r"(\*{1,2}|_{2})(.+?)\1", re.DOTALL)
_EMOJI = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002600-\U000027bf" "\U0001f1e6-\U0001f1ff" "]+",
    flags=re.UNICODE,
)


def clean_narration(text: str) -> str:
    """Strip anything a TTS voice would embarrassingly read out loud."""
    text = _STAGE_DIRECTION.sub(" ", text)
    text = _MARKDOWN_HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _SPEAKER_LABEL.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _EMOJI.sub("", text)
    text = text.replace("—", ", ").replace("–", ", ")
    # Collapse whitespace but keep paragraph breaks, which give TTS its pauses.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def write_debug_bundle(directory: Path, idea: TopicIdea, package: ScriptPackage) -> None:
    """Persist the written material so a failed run can be inspected later."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "topic.json").write_text(idea.model_dump_json(indent=2), encoding="utf-8")
    (directory / "scripts.json").write_text(package.model_dump_json(indent=2), encoding="utf-8")
