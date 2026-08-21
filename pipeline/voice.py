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


def _synthesize_edge(config: Config, text: str, destination: Path) -> Voiceover:
    import edge_tts

    voice = str(config.get("voice.edge_voice", "en-US-AndrewMultilingualNeural"))
    rate = str(config.get("voice.edge_rate", "+0%"))
    pitch = str(config.get("voice.edge_pitch", "+0Hz"))
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
    log.info("Narration: %.1fs, %d word timings, voice=%s", duration, len(words), voice)
    return Voiceover(audio_path=destination, duration=duration, words=words)


# ---------------------------------------------------------------------------
# ElevenLabs (paid, higher quality)
# ---------------------------------------------------------------------------


def _synthesize_elevenlabs(config: Config, text: str, destination: Path) -> Voiceover:
    api_key = config.secrets.require("elevenlabs_api_key", "ELEVENLABS_API_KEY")
    voice_id = str(config.get("voice.elevenlabs_voice_id", "")).strip()
    if not voice_id:
        raise ConfigError("voice.elevenlabs_voice_id must be set when voice.provider is 'elevenlabs'.")

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
