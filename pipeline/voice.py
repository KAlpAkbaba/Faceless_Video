"""Narration synthesis with word-level timings.

Word timings come straight from the TTS engine, so the burned-in captions line
up exactly with the voice without running a speech-recognition model.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from pathlib import Path

import httpx

from .config import Config, ConfigError
from .media import duration_seconds
from .models import Voiceover, WordTiming

log = logging.getLogger(__name__)

# edge-tts reports offsets in 100-nanosecond ticks.
TICKS_PER_SECOND = 10_000_000


class VoiceError(RuntimeError):
    """Raised when narration cannot be synthesised."""


def voice_for(config: Config, speaker: str) -> dict[str, str]:
    """The voice settings for one character."""
    eleven = (config.get("voice.elevenlabs_cast", {}) or {}).get(speaker)
    cast = config.get("voice.cast", {}) or {}
    entry = cast.get(speaker)
    if isinstance(entry, dict) and entry.get("voice"):
        return {
            "voice": str(entry["voice"]),
            "rate": str(entry.get("rate", "+0%")),
            "pitch": str(entry.get("pitch", "+0Hz")),
            "voice_id": str(eleven) if eleven else None,
        }
    if speaker not in ("Narrator",):
        log.warning("No voice configured for %r; using the default.", speaker)
    return {
        "voice": str(config.get("voice.default_voice", "en-US-AnaNeural")),
        "rate": str(config.get("voice.default_rate", "+0%")),
        "pitch": str(config.get("voice.default_pitch", "+0Hz")),
        "voice_id": str(eleven) if eleven else None,
    }


def synthesize_lines(config: Config, lines, destination: Path) -> Voiceover:
    """Voice a dialogue, one character at a time, into a single track.

    Each line is synthesised with its own character's voice and the pieces are
    joined with a beat of silence between them — longer after a question, since
    that pause is where a child answers.
    """
    if not lines:
        raise VoiceError("The script has no spoken lines.")

    provider = config.get("voice.provider", "edge")
    if provider == "silent":
        return _synthesize_silent(config, " ".join(l.text for l in lines), destination)

    from .media import duration_seconds, run_ffmpeg

    stage = destination.parent / f"{destination.stem}_lines"
    stage.mkdir(parents=True, exist_ok=True)

    gap = float(config.get("voice.gap_seconds", 0.28))
    question_gap = float(config.get("voice.question_gap_seconds", 0.75))

    segments: list[Path] = []
    words: list[WordTiming] = []
    cursor = 0.0

    for index, line in enumerate(lines):
        text = line.text.strip()
        if not text:
            continue
        settings = voice_for(config, line.speaker)
        piece = stage / f"{index:03d}_{_slug(line.speaker)}.mp3"

        part = _synthesize_line(config, line, text, piece, settings, provider)
        for word in part.words:
            words.append(WordTiming(word.word, word.start + cursor, word.end + cursor))
        segments.append(piece)
        cursor += part.duration

        # A pause after a question is not decoration; it is the turn the child
        # is being given.
        pause = question_gap if text.rstrip().endswith("?") else gap
        if index < len(lines) - 1 and pause > 0:
            silence = stage / f"{index:03d}_gap.mp3"
            if not silence.exists():
                run_ffmpeg(
                    ["-f", "lavfi", "-i", "anullsrc=channel_layout=mono:sample_rate=24000",
                     "-t", f"{pause:.3f}", "-c:a", "libmp3lame", "-b:a", "64k", str(silence)],
                    label="line gap",
                )
            segments.append(silence)
            cursor += pause

    listing = stage / "lines.txt"
    listing.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8"
    )
    run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", str(listing),
         "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "24000", str(destination)],
        label="join dialogue",
    )

    duration = duration_seconds(destination)
    log.info(
        "Dialogue: %.1fs across %d lines, %d word timings, voices=%s",
        duration, len(lines), len(words),
        ", ".join(sorted({l.speaker for l in lines})),
    )
    return Voiceover(audio_path=destination, duration=duration, words=words)


# ElevenLabs reads these as performance direction rather than speaking them.
EMOTION_TAGS = {
    "excited", "curious", "worried", "sad", "surprised",
    "proud", "gentle", "playful", "nervous", "happy",
}


def _synthesize_line(config: Config, line, text: str, piece: Path, settings: dict, provider: str):
    """Voice one line with the provider in use.

    This dispatch is the point of the function: synthesise_lines used to call
    the edge backend directly, so selecting elevenlabs changed nothing about
    how a dialogue was voiced.
    """
    if provider == "elevenlabs":
        return _synthesize_elevenlabs(
            config,
            _with_emotion(text, getattr(line, "emotion", "")),
            piece,
            voice_id=settings.get("voice_id"),
        )
    # edge-tts has no expression control at all, so the emotion is dropped
    # rather than spoken. Nothing is lost by omitting it; it was never heard.
    return _synthesize_edge(
        config, text, piece,
        voice=settings["voice"], rate=settings["rate"], pitch=settings["pitch"],
    )


def _with_emotion(text: str, emotion: str) -> str:
    emotion = (emotion or "").strip().lower()
    if emotion and emotion != "neutral" and emotion in EMOTION_TAGS:
        return f"[{emotion}] {text}"
    return text


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower()) or "speaker"


def synthesize(config: Config, text: str, destination: Path) -> Voiceover:
    provider = config.get("voice.provider", "edge")
    if provider == "edge":
        return _synthesize_edge(config, text, destination)
    if provider == "elevenlabs":
        return _synthesize_elevenlabs(config, text, destination)
    if provider == "silent":
        return _synthesize_silent(config, text, destination)
    raise ConfigError(f"Unknown voice.provider: {provider!r}")


# ---------------------------------------------------------------------------
# edge-tts (free, no API key)
# ---------------------------------------------------------------------------


def _synthesize_edge(
    config: Config,
    text: str,
    destination: Path,
    *,
    voice: str | None = None,
    rate: str | None = None,
    pitch: str | None = None,
) -> Voiceover:
    import edge_tts

    voice = voice or str(config.get("voice.default_voice", "en-US-AnaNeural"))
    rate = rate or str(config.get("voice.default_rate", "+0%"))
    pitch = pitch or str(config.get("voice.default_pitch", "+0Hz"))
    destination.parent.mkdir(parents=True, exist_ok=True)

    async def run() -> list[WordTiming]:
        communicate = edge_tts.Communicate(
            text,
            voice=voice,
            rate=rate,
            pitch=pitch,
            # The default is SentenceBoundary, which is far too coarse for captions.
            boundary="WordBoundary",
        )
        words: list[WordTiming] = []
        with destination.open("wb") as handle:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    handle.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    start = chunk["offset"] / TICKS_PER_SECOND
                    words.append(
                        WordTiming(
                            word=chunk["text"],
                            start=start,
                            end=start + chunk["duration"] / TICKS_PER_SECOND,
                        )
                    )
        return words

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            words = asyncio.run(run())
            if destination.stat().st_size < 2048:
                raise VoiceError("edge-tts produced an empty audio file.")
            break
        except Exception as exc:  # edge-tts raises a wide range of network errors
            last_error = exc
            log.warning("edge-tts attempt %d/3 failed: %s", attempt, exc)
            destination.unlink(missing_ok=True)
            time.sleep(2 * attempt)
    else:
        raise VoiceError(f"edge-tts failed after 3 attempts: {last_error}") from last_error

    duration = duration_seconds(destination)
    log.debug("Line: %.1fs, %d words, voice=%s", duration, len(words), voice)
    return Voiceover(audio_path=destination, duration=duration, words=words)


# ---------------------------------------------------------------------------
# ElevenLabs (paid, higher quality)
# ---------------------------------------------------------------------------


def _synthesize_elevenlabs(
    config: Config, text: str, destination: Path, *, voice_id: str | None = None
) -> Voiceover:
    api_key = config.secrets.require("elevenlabs_api_key", "ELEVENLABS_API_KEY")
    voice_id = (voice_id or str(config.get("voice.elevenlabs_voice_id", ""))).strip()
    if not voice_id:
        raise ConfigError(
            "No ElevenLabs voice for this speaker. Add one per character under "
            "voice.elevenlabs_cast, or set voice.elevenlabs_voice_id as a fallback."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    payload = {
        "text": text,
        "model_id": str(config.get("voice.elevenlabs_model", "eleven_multilingual_v2")),
    }

    with httpx.Client(timeout=httpx.Timeout(300.0, connect=20.0)) as client:
        response = client.post(url, headers={"xi-api-key": api_key}, json=payload)
        if response.status_code >= 400:
            raise VoiceError(f"ElevenLabs returned HTTP {response.status_code}: {response.text[:400]}")
        body = response.json()

    audio_b64 = body.get("audio_base64")
    if not audio_b64:
        raise VoiceError("ElevenLabs response contained no audio_base64 field.")
    destination.write_bytes(base64.b64decode(audio_b64))

    alignment = body.get("alignment") or body.get("normalized_alignment") or {}
    words = _words_from_character_alignment(
        alignment.get("characters", []),
        alignment.get("character_start_times_seconds", []),
        alignment.get("character_end_times_seconds", []),
    )
    duration = duration_seconds(destination)
    log.info("Narration: %.1fs, %d word timings, voice=elevenlabs/%s", duration, len(words), voice_id)
    return Voiceover(audio_path=destination, duration=duration, words=words)


def _words_from_character_alignment(
    characters: list[str], starts: list[float], ends: list[float]
) -> list[WordTiming]:
    """Fold ElevenLabs' per-character alignment into per-word timings."""
    if not (characters and len(characters) == len(starts) == len(ends)):
        return []
    words: list[WordTiming] = []
    buffer = ""
    start: float | None = None
    end = 0.0
    for char, char_start, char_end in zip(characters, starts, ends):
        if char.isspace():
            if buffer:
                words.append(WordTiming(word=buffer, start=start or 0.0, end=end))
                buffer, start = "", None
            continue
        if start is None:
            start = char_start
        buffer += char
        end = char_end
    if buffer:
        words.append(WordTiming(word=buffer, start=start or 0.0, end=end))
    return words


# ---------------------------------------------------------------------------
# silent — offline dry-run mode
# ---------------------------------------------------------------------------


def _synthesize_silent(config: Config, text: str, destination: Path) -> Voiceover:
    """Produce a silent track of the length the narration would take.

    No network, no cost. Use it to rehearse the whole pipeline — pacing, cuts,
    caption placement, upload scheduling — without spending anything.
    """
    from .media import run_ffmpeg
    from .writer import WORDS_PER_SECOND

    words = _WORD_RE.findall(text)
    duration = max(2.0, len(words) / WORDS_PER_SECOND)
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-f", "lavfi",
            "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t", f"{duration:.3f}",
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(destination),
        ],
        label="silent narration",
    )
    log.warning("voice.provider is 'silent' — this run produces a video with no narration.")
    return Voiceover(
        audio_path=destination,
        duration=duration,
        words=estimate_word_timings(text, duration),
    )


# ---------------------------------------------------------------------------
# Fallback timings
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"\S+")


def estimate_word_timings(text: str, duration: float) -> list[WordTiming]:
    """Spread words evenly across the audio when the engine gave no timings.

    Only a safety net — captions drift on long text, so it is used when a
    provider returns nothing rather than as anyone's first choice.
    """
    tokens = _WORD_RE.findall(text)
    if not tokens or duration <= 0:
        return []
    # Weight by length so long words hold the screen longer than short ones.
    weights = [max(1, len(token)) for token in tokens]
    total = sum(weights)
    timings: list[WordTiming] = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        span = duration * weight / total
        timings.append(WordTiming(word=token, start=cursor, end=cursor + span))
        cursor += span
    return timings
