"""Turn shot prompts into rendered B-roll clips, a few at a time."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import Config
from .models import ClipAsset, Shot
from .providers.base import ClipRequest, VideoGenerationError, VideoProvider
from .references import ReferenceLibrary

log = logging.getLogger(__name__)


def build_clip_prompt(shot: Shot, style_suffix: str) -> str:
    """Append the channel's house style so every clip looks like the same film."""
    prompt = shot.prompt.strip().rstrip(".")
    if style_suffix:
        return f"{prompt}. {style_suffix.strip()}"
    return prompt


def generate_clips(
    config: Config,
    provider: VideoProvider,
    shots: list[Shot],
    *,
    work_dir: Path,
    aspect_ratio: str,
    resolution: str,
    prefix: str,
    concurrency: int = 3,
    references: ReferenceLibrary | None = None,
) -> list[ClipAsset]:
    """Render every shot, in parallel, tolerating a few individual failures.

    A single refused prompt should not throw away a paid run, so failures are
    logged and skipped; the caller decides whether enough clips survived.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    seconds = float(config.get("video.clip_seconds", 8))
    style_suffix = str(config.get("video.style_suffix", ""))
    negative = str(config.get("video.negative_prompt", ""))

    # Picked up front, not inside the worker: the library rotates through
    # matches, and threads would make that order non-deterministic.
    frames = (
        {i: references.pick(shot.characters) for i, shot in enumerate(shots)}
        if references
        else {}
    )

    def render(index: int, shot: Shot) -> ClipAsset:
        destination = work_dir / f"{prefix}_{index:02d}.mp4"
        if destination.exists() and destination.stat().st_size > 1024:
            # Resume a partially completed run without paying twice.
            log.info("Reusing existing clip %s", destination.name)
            return ClipAsset(index=index, path=destination, prompt=shot.prompt, seconds=seconds)
        request = ClipRequest(
            prompt=build_clip_prompt(shot, style_suffix),
            seconds=seconds,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            negative_prompt=negative,
            # Deterministic per shot, so a retry of the same run reproduces the look.
            seed=abs(hash((prefix, index))) % 2_000_000_000,
            reference_image=frames.get(index),
        )
        provider.generate(request, destination)
        return ClipAsset(index=index, path=destination, prompt=shot.prompt, seconds=seconds)

    assets: list[ClipAsset] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(render, i, shot): (i, shot) for i, shot in enumerate(shots)}
        for future in as_completed(futures):
            index, shot = futures[future]
            try:
                assets.append(future.result())
            except (VideoGenerationError, OSError) as exc:
                failures.append(f"shot {index} ({shot.beat_label}): {exc}")
                log.error("Clip %d failed: %s", index, exc)

    assets.sort(key=lambda asset: asset.index)
    if failures:
        log.warning("%d of %d clips failed:\n  %s", len(failures), len(shots), "\n  ".join(failures))
    if not assets:
        raise VideoGenerationError(
            "Every clip failed to generate; refusing to build a video.\n  " + "\n  ".join(failures)
        )
    return assets
