"""Assemble narration, B-roll and captions into a finished video.

The clip pool is always shorter than the narration, so the timeline reuses
clips: each is turned into a seamless forward-then-reverse loop, segments are
cut from the pool round-robin (so a clip never follows itself and repeats sit
far apart), and each repeat starts at a different offset.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .media import duration_seconds, run_ffmpeg
from .models import ClipAsset, RenderRequest, RenderResult, Voiceover

log = logging.getLogger(__name__)

# Video keeps running briefly after the last word so it does not cut dead.
TAIL_SECONDS = 1.2
FADE_OUT_SECONDS = 0.8
# Above this many segments the xfade filter graph gets unwieldy; hard-cut instead.
MAX_XFADE_SEGMENTS = 60


@dataclass
class Segment:
    """One slot on the timeline, filled by one clip."""

    index: int
    clip_index: int
    duration: float
    source_offset: float


def plan_segments(
    total_duration: float,
    clip_count: int,
    *,
    segment_seconds: float,
    transition_seconds: float,
    clip_seconds: float,
) -> list[Segment]:
    """Work out which clip fills which slice of the timeline.

    With transitions, n segments of length L cover n*L - (n-1)*t seconds, so
    n = ceil((target - t) / (L - t)).
    """
    if clip_count <= 0:
        raise ValueError("Cannot plan a timeline with no clips.")
    if total_duration <= 0:
        raise ValueError("Cannot plan a timeline with no duration.")

    length = max(2.0, float(segment_seconds))
    transition = max(0.0, min(float(transition_seconds), length / 3))
    effective = length - transition
    count = max(1, int(-(-(total_duration - transition) // effective)))  # ceil

    # A boomerang is twice the clip, and each repeat enters at a new offset so
    # the same footage does not replay identically.
    boomerang = max(1.0, clip_seconds * 2)
    seen: dict[int, int] = {}

    segments: list[Segment] = []
    for index in range(count):
        clip_index = index % clip_count
        repeat = seen.get(clip_index, 0)
        seen[clip_index] = repeat + 1
        segments.append(
            Segment(
                index=index,
                clip_index=clip_index,
                duration=length,
                source_offset=round((repeat * 3.0) % boomerang, 3),
            )
        )
    return segments


def timeline_duration(segments: list[Segment], transition_seconds: float) -> float:
    if not segments:
        return 0.0
    total = sum(s.duration for s in segments)
    return total - transition_seconds * (len(segments) - 1)


# ---------------------------------------------------------------------------
# ffmpeg stages
# ---------------------------------------------------------------------------


def build_boomerang(clip: ClipAsset, destination: Path, fps: int) -> Path:
    """Forward then reverse, so the clip can loop with no visible seam."""
    if destination.exists() and destination.stat().st_size > 1024:
        return destination
    run_ffmpeg(
        [
            "-i", str(clip.path),
            "-an",
            "-filter_complex",
            f"[0:v]fps={fps},setpts=PTS-STARTPTS,split[fwd][tmp];[tmp]reverse,setpts=PTS-STARTPTS[rev];"
            "[fwd][rev]concat=n=2:v=1:a=0[out]",
            "-map", "[out]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            str(destination),
        ],
        label=f"boomerang {clip.path.name}",
    )
    return destination


def render_segment(
    source: Path,
    destination: Path,
    *,
    width: int,
    height: int,
    duration: float,
    offset: float,
    fps: int,
) -> Path:
    """Cut one timeline slot, scaled and cropped to fill the frame."""
    run_ffmpeg(
        [
            # Loop the boomerang indefinitely; -t decides how much we keep.
            "-stream_loop", "-1",
            "-ss", f"{offset:.3f}",
            "-i", str(source),
            "-t", f"{duration:.3f}",
            "-an",
            "-vf",
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},format=yuv420p,setpts=PTS-STARTPTS",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            str(destination),
        ],
        label=f"segment {destination.name}",
    )
    return destination


def concat_segments(
    segments: list[Path],
    destination: Path,
    *,
    transition_seconds: float,
    work_dir: Path,
) -> Path:
    """Join the segments, cross-fading when the graph stays manageable."""
    if len(segments) == 1:
        run_ffmpeg(["-i", str(segments[0]), "-c", "copy", str(destination)], label="single segment")
        return destination

    if transition_seconds <= 0 or len(segments) > MAX_XFADE_SEGMENTS:
        listing = work_dir / "segments.txt"
        listing.write_text(
            "".join(f"file '{path.resolve().as_posix()}'\n" for path in segments), encoding="utf-8"
        )
        run_ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(destination)],
            label="concat segments",
        )
        return destination

    durations = [duration_seconds(path) for path in segments]
    inputs: list[str] = []
    for path in segments:
        inputs.extend(["-i", str(path)])

    # Each xfade consumes `transition` seconds of overlap, so every offset is
    # the running total minus the transitions already spent.
    steps: list[str] = []
    current = "[0:v]"
    elapsed = durations[0]
    for index in range(1, len(segments)):
        offset = elapsed - transition_seconds
        label = f"[x{index}]"
        steps.append(
            f"{current}[{index}:v]xfade=transition=fade:duration={transition_seconds:.3f}:"
            f"offset={max(0.0, offset):.3f}{label}"
        )
        current = label
        elapsed = elapsed - transition_seconds + durations[index]

    run_ffmpeg(
        [
            *inputs,
            "-filter_complex", ";".join(steps),
            "-map", current,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            str(destination),
        ],
        label="crossfade segments",
    )
    return destination


def escape_filter_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    text = str(path)
    return text.replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")


def pick_music(config: Config) -> Path | None:
    if not config.get("music.enabled", False):
        return None
    directory = Path(config.get("music.directory", "assets/music"))
    if not directory.is_absolute():
        from .config import REPO_ROOT

        directory = REPO_ROOT / directory
    if not directory.exists():
        return None
    tracks = sorted(
        p for p in directory.iterdir() if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".ogg", ".flac")
    )
    if not tracks:
        log.info("music.enabled is true but %s holds no audio files — continuing without a bed.", directory)
        return None
    return random.choice(tracks)


def finalize(
    config: Config,
    silent_video: Path,
    voiceover: Voiceover,
    destination: Path,
    *,
    captions: Path | None,
    duration: float,
) -> Path:
    """Burn captions, mix the audio bed, fade out, and write the deliverable."""
    music = pick_music(config)

    inputs = ["-i", str(silent_video), "-i", str(voiceover.audio_path)]
    if music:
        inputs.extend(["-stream_loop", "-1", "-i", str(music)])

    video_chain = [f"trim=duration={duration:.3f}", "setpts=PTS-STARTPTS"]
    if captions:
        video_chain.append(f"ass='{escape_filter_path(captions)}'")
    video_chain.append(f"fade=t=out:st={max(0.0, duration - FADE_OUT_SECONDS):.3f}:d={FADE_OUT_SECONDS}")
    filters = [f"[0:v]{','.join(video_chain)}[v]"]

    # -14 LUFS is what YouTube normalises to; getting there ourselves avoids
    # having the platform quietly turn the whole video down.
    filters.append("[1:a]loudnorm=I=-14:TP=-1.5:LRA=11,apad[voice]")
    if music:
        volume_db = float(config.get("music.volume_db", -26))
        fade = float(config.get("music.fade_seconds", 2.5))
        filters.append(
            f"[2:a]volume={volume_db}dB,afade=t=in:st=0:d={fade:.2f},"
            f"afade=t=out:st={max(0.0, duration - fade):.3f}:d={fade:.2f}[bed]"
        )
        # normalize=0 keeps the narration at full level instead of halving it.
        filters.append("[voice][bed]amix=inputs=2:duration=first:normalize=0[amix]")
        audio_label = "[amix]"
    else:
        audio_label = "[voice]"
    filters.append(f"{audio_label}atrim=duration={duration:.3f},asetpts=PTS-STARTPTS[a]")

    destination.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-level", "4.1",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            "-t", f"{duration:.3f}",
            str(destination),
        ],
        label=f"finalize {destination.name}",
    )
    return destination


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def assemble(config: Config, request: RenderRequest, work_dir: Path) -> RenderResult:
    """Build one finished cut from clips, narration and captions."""
    from .captions import write_captions

    fps = int(config.get("video.fps", 25))
    segment_seconds = float(config.get("video.segment_seconds", 11))
    transition = float(config.get("video.transition_seconds", 0.5))
    clip_seconds = float(config.get("video.clip_seconds", 8))

    stage = work_dir / request.kind
    stage.mkdir(parents=True, exist_ok=True)

    target = request.voiceover.duration + TAIL_SECONDS
    segments = plan_segments(
        target,
        len(request.clips),
        segment_seconds=segment_seconds,
        transition_seconds=transition,
        clip_seconds=clip_seconds,
    )
    log.info(
        "%s timeline: %.1fs of narration -> %d segments from %d clips",
        request.kind, request.voiceover.duration, len(segments), len(request.clips),
    )

    boomerangs = {
        clip.index: build_boomerang(clip, stage / f"loop_{clip.index:02d}.mp4", fps)
        for clip in request.clips
    }
    ordered_clips = sorted(request.clips, key=lambda c: c.index)

    segment_paths: list[Path] = []
    for segment in segments:
        clip = ordered_clips[segment.clip_index % len(ordered_clips)]
        segment_paths.append(
            render_segment(
                boomerangs[clip.index],
                stage / f"seg_{segment.index:03d}.mp4",
                width=request.width,
                height=request.height,
                duration=segment.duration,
                offset=segment.source_offset,
                fps=fps,
            )
        )

    silent = concat_segments(
        segment_paths, stage / "silent.mp4", transition_seconds=transition, work_dir=stage
    )

    caption_file = None
    if request.burn_captions:
        caption_file = write_captions(
            config,
            request.voiceover.words,
            stage / "captions.ass",
            width=request.width,
            height=request.height,
            vertical=request.height > request.width,
        )

    final = finalize(
        config,
        silent,
        request.voiceover,
        request.output_path,
        captions=caption_file,
        duration=target,
    )

    return RenderResult(
        kind=request.kind,
        video_path=final,
        thumbnail_path=None,
        duration=duration_seconds(final),
        script=request.script,
    )
