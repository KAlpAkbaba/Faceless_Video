"""Thin, checked wrappers around ffmpeg and ffprobe."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class MediaError(RuntimeError):
    """Raised when ffmpeg/ffprobe is missing or fails."""


def require_ffmpeg() -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise MediaError(
            f"Required tool(s) not on PATH: {', '.join(missing)}. "
            "Install with `apt-get install ffmpeg` (or `brew install ffmpeg`)."
        )


def run_ffmpeg(args: list[str], *, label: str = "ffmpeg") -> None:
    """Run ffmpeg, surfacing only the tail of stderr on failure."""
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("%s: %s", label, " ".join(command))
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-25:])
        raise MediaError(f"{label} failed (exit {result.returncode}):\n{tail}")


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MediaError(f"ffprobe failed for {path}:\n{result.stderr.strip()[:400]}")
    return json.loads(result.stdout)


def duration_seconds(path: Path) -> float:
    info = probe(path)
    value = info.get("format", {}).get("duration")
    if value is None:
        for stream in info.get("streams", []):
            if stream.get("duration"):
                value = stream["duration"]
                break
    if value is None:
        raise MediaError(f"Could not determine duration of {path}")
    return float(value)


def video_dimensions(path: Path) -> tuple[int, int]:
    for stream in probe(path).get("streams", []):
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise MediaError(f"No video stream in {path}")
