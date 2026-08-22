"""The daily run: topic -> scripts -> voice -> clips -> cuts -> YouTube."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .assemble import assemble
from .budget import enforce_budget, estimate_run_cost, shots_for_duration
from .clips import generate_clips
from .config import REPO_ROOT, Config
from .media import require_ffmpeg
from .models import RenderRequest, RenderResult
from .providers import build_provider
from .providers.ltx import resolve_dimensions
from .state import History
from .thumbnail import build_thumbnail
from .voice import synthesize
from .writer import Writer, write_debug_bundle

log = logging.getLogger(__name__)

# Roughly how many characters of script a second of narration consumes; used
# for the pre-flight cost estimate, before any script exists.
CHARS_PER_SECOND = 14


@dataclass
class RunOptions:
    only: str = "both"  # both | longform | shorts
    upload: bool = True
    work_dir: Path | None = None
    output_dir: Path | None = None


@dataclass
class RunReport:
    slug: str
    topic_title: str
    results: list[RenderResult]
    video_ids: dict[str, str]
    estimated_cost: float

    def summary(self) -> str:
        lines = [f"Topic: {self.topic_title}  ({self.slug})", f"Estimated spend: ${self.estimated_cost:.2f}"]
        for result in self.results:
            video_id = self.video_ids.get(result.kind)
            location = f"https://youtu.be/{video_id}" if video_id else str(result.video_path)
            lines.append(f"  {result.kind:<9} {result.duration:6.1f}s  {location}")
        return "\n".join(lines)


def wants(options: RunOptions, kind: str) -> bool:
    return options.only in ("both", kind)


def run(config: Config, options: RunOptions | None = None) -> RunReport:
    options = options or RunOptions()
    require_ffmpeg()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    work_dir = options.work_dir or (REPO_ROOT / "work" / stamp)
    output_dir = options.output_dir or (REPO_ROOT / "out" / stamp)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    do_longform = bool(config.get("longform.enabled", True)) and wants(options, "longform")
    do_shorts = bool(config.get("shorts.enabled", True)) and wants(options, "shorts")
    if not (do_longform or do_shorts):
        raise RuntimeError("Nothing enabled to build for this run.")

    # Shorts can reuse the long-form clips only when there are long-form clips.
    shorts_strategy = str(config.get("shorts.clip_strategy", "crop"))
    if do_shorts and shorts_strategy == "crop" and not do_longform:
        log.info("Shorts-only run: clip_strategy 'crop' has nothing to crop, generating vertical clips.")
        shorts_strategy = "regenerate"

    # -- pre-flight cost check, before a single paid call ------------------
    reuse = bool(config.get("video.reuse_clips", True))
    clip_seconds = float(config.get("video.clip_seconds", 8))
    transition = float(config.get("video.transition_seconds", 0.5))

    if reuse:
        # Clips are looped across the timeline, so the count is a quality knob.
        longform_clips = int(config.get("video.max_clips", 12)) if do_longform else 0
        shorts_clips = (
            int(config.get("shorts.max_clips", 6))
            if (do_shorts and shorts_strategy == "regenerate")
            else 0
        )
    else:
        # Every shot plays once, so the runtime dictates the count.
        longform_clips = (
            shots_for_duration(float(config.get("longform.target_seconds", 360)), clip_seconds, transition)
            if do_longform
            else 0
        )
        shorts_clips = (
            shots_for_duration(float(config.get("shorts.target_seconds", 50)), clip_seconds, transition)
            if do_shorts
            else 0
        )
        log.info(
            "reuse_clips is off: %d long-form and %d shorts shots derived from runtime",
            longform_clips, shorts_clips,
        )
    model = str(config.require("video.model"))
    prices = config.get("budget.ltx_price_per_second", {}) or {}
    price = float(prices.get(model, 0.04))

    narration_seconds = (float(config.get("longform.target_seconds", 360)) if do_longform else 0) + (
        float(config.get("shorts.target_seconds", 50)) if do_shorts else 0
    )
    estimate = estimate_run_cost(
        longform_clips=longform_clips,
        shorts_clips=shorts_clips,
        clip_seconds=clip_seconds,
        price_per_second=price,
        narration_chars=int(narration_seconds * CHARS_PER_SECOND),
        voice_provider=str(config.get("voice.provider", "edge")),
    )
    log.info("Cost estimate for this run:\n%s", estimate.render())
    enforce_budget(estimate, float(config.get("budget.max_usd_per_run", 5.0)))

    # -- write ------------------------------------------------------------
    history = History(config.history_path, keep_last=int(config.get("state.dedupe_last_n", 120)))
    writer = Writer(config)
    idea = writer.pick_topic(history.recent_titles(), history.recent_slugs())
    package = writer.write_scripts(idea)
    write_debug_bundle(work_dir, idea, package)

    provider = build_provider(config)
    results: list[RenderResult] = []
    longform_clip_assets = []

    try:
        # -- long-form ----------------------------------------------------
        if do_longform:
            aspect = str(config.get("longform.aspect_ratio", "16:9"))
            resolution = str(config.get("longform.resolution", "1080p"))
            width, height = resolve_dimensions(resolution, aspect)

            voiceover = synthesize(config, package.longform.narration, work_dir / "longform" / "narration.mp3")
            longform_clip_assets = generate_clips(
                config,
                provider,
                package.longform.shots[:longform_clips],
                work_dir=work_dir / "clips_longform",
                aspect_ratio=aspect,
                resolution=resolution,
                prefix="lf",
                concurrency=int(config.get("video.concurrency", 3)),
            )
            request = RenderRequest(
                kind="longform",
                script=package.longform,
                voiceover=voiceover,
                clips=longform_clip_assets,
                width=width,
                height=height,
                burn_captions=bool(config.get("captions.enabled_longform", False)),
                output_path=output_dir / f"{idea.slug}-longform.mp4",
            )
            result = assemble(config, request, work_dir)
            result.thumbnail_path = build_thumbnail(
                result.video_path,
                package.longform.thumbnail_text,
                output_dir / f"{idea.slug}-thumb.jpg",
                work_dir,
            )
            results.append(result)

        # -- shorts -------------------------------------------------------
        if do_shorts:
            aspect = str(config.get("shorts.aspect_ratio", "9:16"))
            resolution = str(config.get("longform.resolution", "1080p"))
            width, height = resolve_dimensions(resolution, aspect)

            voiceover = synthesize(config, package.shorts.narration, work_dir / "shorts" / "narration.mp3")
            if shorts_strategy == "regenerate":
                shorts_assets = generate_clips(
                    config,
                    provider,
                    package.shorts.shots[: int(config.get("shorts.max_clips", 6))],
                    work_dir=work_dir / "clips_shorts",
                    aspect_ratio=aspect,
                    resolution=resolution,
                    prefix="sh",
                    concurrency=int(config.get("video.concurrency", 3)),
                )
            else:
                # Centre-crop the long-form footage; the assembler handles the crop.
                shorts_assets = longform_clip_assets[: int(config.get("shorts.max_clips", 6))]
                log.info("Shorts reuse %d long-form clips (centre-cropped, no extra cost).", len(shorts_assets))

            request = RenderRequest(
                kind="shorts",
                script=package.shorts,
                voiceover=voiceover,
                clips=shorts_assets,
                width=width,
                height=height,
                burn_captions=bool(config.get("captions.enabled_shorts", True)),
                output_path=output_dir / f"{idea.slug}-shorts.mp4",
            )
            results.append(assemble(config, request, work_dir))
    finally:
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    # -- publish ----------------------------------------------------------
    video_ids: dict[str, str] = {}
    if options.upload:
        from .youtube import build_client, publish_result

        youtube = build_client(config)
        for result in results:
            video_ids[result.kind] = publish_result(config, youtube, result)
    else:
        log.info("Upload skipped (--no-upload); files are in %s", output_dir)

    history.add(idea.slug, package.longform.title, idea.angle, list(video_ids.values()))
    history.save()

    return RunReport(
        slug=idea.slug,
        topic_title=package.longform.title,
        results=results,
        video_ids=video_ids,
        estimated_cost=estimate.total,
    )
