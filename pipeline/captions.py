"""Word-synchronised burned-in captions, written as an ASS subtitle file.

Shorts live or die on captions, so the active word is highlighted as it is
spoken. Timings come from the TTS engine, so nothing drifts.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .config import Config
from .models import WordTiming

log = logging.getLogger(__name__)

MAX_WORDS_PER_CUE = 3
MAX_CUE_SECONDS = 2.4
# A cue that ends the instant the word does looks like a flicker.
CUE_TAIL_SECONDS = 0.08

# Words that read badly alone on screen, so a chunk never ends on one.
_TRAILING_ORPHANS = {
    "a", "an", "the", "of", "to", "in", "on", "at", "and", "but", "or",
    "for", "with", "by", "from", "as", "is", "was", "were", "that", "it",
}


def format_timestamp(seconds: float) -> str:
    """ASS wants H:MM:SS.cc — hours are not zero-padded."""
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{int(secs):02d}.{int(round((secs % 1) * 100)):02d}"


def escape_ass(text: str) -> str:
    """Neutralise the characters ASS treats as markup."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", " ")


def chunk_words(words: list[WordTiming], max_words: int = MAX_WORDS_PER_CUE) -> list[list[WordTiming]]:
    """Group word timings into short on-screen cues."""
    chunks: list[list[WordTiming]] = []
    current: list[WordTiming] = []

    for word in words:
        if not current:
            current = [word]
            continue

        span = word.end - current[0].start
        would_be_full = len(current) >= max_words
        too_long = span > MAX_CUE_SECONDS
        # A sentence ending is a natural place to cut.
        ends_sentence = bool(re.search(r"[.!?]$", current[-1].word))

        # The orphan rule can only stretch a cue so far before it stops being
        # readable, so a hard cap overrides it.
        forced = len(current) >= max_words + 2
        if forced or ((would_be_full or too_long or ends_sentence) and not _is_orphan(current[-1].word)):
            chunks.append(current)
            current = [word]
        else:
            current.append(word)

    if current:
        chunks.append(current)
    return chunks


def _is_orphan(word: str) -> bool:
    return re.sub(r"[^\w']", "", word).lower() in _TRAILING_ORPHANS


def build_ass(
    words: list[WordTiming],
    *,
    width: int,
    height: int,
    font: str = "DejaVu Sans",
    font_size: int = 78,
    primary_colour: str = "&H00FFFFFF",
    highlight_colour: str = "&H0000E5FF",
    outline: int = 4,
    margin_v: int = 260,
    max_words: int = MAX_WORDS_PER_CUE,
) -> str:
    """Render word timings into an ASS file that highlights the spoken word."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{font_size},{primary_colour},&H000000FF,&H00000000,&H90000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,{int(width * 0.06)},{int(width * 0.06)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    chunks = chunk_words(words, max_words=max_words)
    events: list[str] = []

    for chunk_index, chunk in enumerate(chunks):
        # A cue must never outlive the next one, or libass draws both at once.
        next_chunk_start = (
            chunks[chunk_index + 1][0].start if chunk_index + 1 < len(chunks) else None
        )
        for position, active in enumerate(chunk):
            parts = []
            for index, word in enumerate(chunk):
                colour = highlight_colour if index == position else primary_colour
                parts.append(f"{{\\c{colour}}}{escape_ass(word.word)}")
            text = " ".join(parts)

            start = active.start
            is_last = position == len(chunk) - 1
            if is_last:
                # Hold the final word a beat longer so the cue does not blink out,
                # but stop short of the next cue.
                end = active.end + CUE_TAIL_SECONDS
                if next_chunk_start is not None:
                    end = min(end, next_chunk_start - 0.01)
            else:
                # Bridge any silence between words so the line stays on screen.
                end = max(active.end, chunk[position + 1].start)
            if end <= start:
                end = start + 0.12

            events.append(
                f"Dialogue: 0,{format_timestamp(start)},{format_timestamp(end)},Caption,,0,0,0,,{text}"
            )

    return header + "\n".join(events) + "\n"


def write_captions(
    config: Config,
    words: list[WordTiming],
    destination: Path,
    *,
    width: int,
    height: int,
    vertical: bool,
) -> Path | None:
    """Write the ASS file for one cut, or None when there is nothing to write."""
    if not words:
        log.warning("No word timings available — skipping burned-in captions.")
        return None

    font_size = int(
        config.get("captions.font_size_shorts", 78) if vertical else config.get("captions.font_size_longform", 54)
    )
    content = build_ass(
        words,
        width=width,
        height=height,
        font=str(config.get("captions.font", "DejaVu Sans")),
        font_size=font_size,
        primary_colour=str(config.get("captions.primary_colour", "&H00FFFFFF")),
        highlight_colour=str(config.get("captions.highlight_colour", "&H0000E5FF")),
        outline=int(config.get("captions.outline", 4)),
        margin_v=int(config.get("captions.margin_v", 260) if vertical else config.get("captions.margin_v_longform", 90)),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    log.info("Captions written: %s", destination.name)
    return destination
