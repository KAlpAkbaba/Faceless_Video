"""Shared plumbing for text-to-video providers.

The response shapes of hosted video APIs move around between versions, so the
helpers here locate a job id or an output URL by *searching* the JSON rather
than by indexing a path that a vendor may rename.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

import httpx

log = logging.getLogger(__name__)

ID_KEYS = ("id", "job_id", "jobId", "task_id", "taskId", "generation_id", "generationId", "request_id", "requestId")
URL_KEYS = ("url", "video_url", "videoUrl", "output_url", "outputUrl", "download_url", "downloadUrl", "signed_url")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".m4v")

TERMINAL_SUCCESS = {"completed", "complete", "succeeded", "success", "finished", "done", "ok", "ready"}
TERMINAL_FAILURE = {"failed", "failure", "error", "errored", "canceled", "cancelled", "rejected", "timeout"}


class VideoGenerationError(RuntimeError):
    """Raised when a provider cannot deliver a clip."""


@dataclass
class ClipRequest:
    prompt: str
    seconds: float
    aspect_ratio: str
    resolution: str
    negative_prompt: str
    seed: int | None


class VideoProvider(Protocol):
    name: str

    def generate(self, request: ClipRequest, destination: Path) -> Path:
        """Generate one clip and write it to `destination`."""

    def probe(self, prompt: str, *, resolution: str, seconds: float) -> dict[str, Any]:
        """Submit one job and return the raw responses, for diagnostics."""


# ---------------------------------------------------------------------------
# JSON spelunking
# ---------------------------------------------------------------------------


def walk(node: Any) -> Iterator[tuple[str, Any]]:
    """Yield every (key, value) pair anywhere inside a nested JSON structure."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def find_job_id(payload: Any) -> str | None:
    """Find the identifier a provider wants us to poll on."""
    if isinstance(payload, str):
        return payload or None
    for key, value in walk(payload):
        if key in ID_KEYS and isinstance(value, str) and value.strip():
            return value.strip()
    return None


def find_video_url(payload: Any) -> str | None:
    """Find a URL that points at a rendered video file."""
    candidates: list[str] = []
    for key, value in walk(payload):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            lowered = value.split("?", 1)[0].lower()
            if lowered.endswith(VIDEO_EXTENSIONS):
                # An unambiguous video file wins immediately.
                return value
            if key in URL_KEYS:
                candidates.append(value)
    return candidates[0] if candidates else None


def find_status(payload: Any) -> str | None:
    for key, value in walk(payload):
        if key in ("status", "state", "job_status") and isinstance(value, str):
            return value.strip().lower()
    return None


def find_error_message(payload: Any) -> str:
    parts: list[str] = []
    for key, value in walk(payload):
        if key in ("error", "message", "detail", "failure_reason", "error_message") and isinstance(value, str):
            if value.strip():
                parts.append(value.strip())
    return "; ".join(dict.fromkeys(parts))[:500] or "no error message returned"


# ---------------------------------------------------------------------------
# Polling and download
# ---------------------------------------------------------------------------


def poll_until_done(
    fetch: Callable[[], dict[str, Any]],
    *,
    timeout_seconds: float,
    initial_interval: float = 3.0,
    max_interval: float = 20.0,
    label: str = "job",
) -> dict[str, Any]:
    """Poll `fetch` with backoff until the payload looks terminal.

    A payload counts as done when it carries a terminal status, or when it
    simply contains a video URL — some providers drop the status field once
    the result is ready.
    """
    deadline = time.monotonic() + timeout_seconds
    interval = initial_interval
    last: dict[str, Any] = {}

    while time.monotonic() < deadline:
        time.sleep(interval)
        last = fetch()
        status = find_status(last)

        if status in TERMINAL_FAILURE:
            raise VideoGenerationError(f"{label} failed ({status}): {find_error_message(last)}")
        if status in TERMINAL_SUCCESS or (status is None and find_video_url(last)):
            return last
        if find_video_url(last) and status not in ("processing", "running", "in_progress", "queued", "pending", "starting"):
            return last

        interval = min(interval * 1.5, max_interval)

    raise VideoGenerationError(
        f"{label} did not finish within {timeout_seconds:.0f}s (last status: {find_status(last)!r})"
    )


def download(client: httpx.Client, url: str, destination: Path) -> Path:
    """Stream a rendered clip to disk."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".part")
    with client.stream("GET", url, timeout=300.0, follow_redirects=True) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_bytes(chunk_size=1 << 16):
                handle.write(chunk)
    if tmp.stat().st_size < 1024:
        tmp.unlink(missing_ok=True)
        raise VideoGenerationError(f"Downloaded clip from {url} is implausibly small.")
    tmp.replace(destination)
    log.info("Downloaded %s (%.1f MB)", destination.name, destination.stat().st_size / 1e6)
    return destination


def raise_for_api_error(response: httpx.Response, context: str) -> dict[str, Any]:
    """Turn a non-2xx into a readable error, and parse the body on success."""
    if response.status_code >= 400:
        body = response.text[:600]
        raise VideoGenerationError(f"{context} returned HTTP {response.status_code}: {body}")
    try:
        return response.json()
    except ValueError as exc:
        raise VideoGenerationError(f"{context} returned non-JSON body: {response.text[:300]}") from exc
