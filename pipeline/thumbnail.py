"""Thumbnail generation: pull a frame from the finished cut and label it."""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from .media import duration_seconds, run_ffmpeg

log = logging.getLogger(__name__)

THUMBNAIL_SIZE = (1280, 720)
# YouTube rejects thumbnails over 2 MB.
MAX_BYTES = 2 * 1024 * 1024

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    log.warning("No bold TrueType font found; falling back to the bitmap default.")
    return ImageFont.load_default()


def grab_frame(video: Path, destination: Path, *, at_fraction: float = 0.28) -> Path:
    """Pull a still from a point in the video that is past the opening fade."""
    timestamp = max(1.0, duration_seconds(video) * at_fraction)
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ["-ss", f"{timestamp:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(destination)],
        label="thumbnail frame",
    )
    return destination


def compose(frame: Path, text: str, destination: Path) -> Path:
    """Crop to 16:9, darken the lower half, and set the headline over it."""
    image = Image.open(frame).convert("RGB")

    # Cover-fit to 1280x720 without distorting.
    target_ratio = THUMBNAIL_SIZE[0] / THUMBNAIL_SIZE[1]
    width, height = image.size
    if width / height > target_ratio:
        new_width = int(height * target_ratio)
        left = (width - new_width) // 2
        image = image.crop((left, 0, left + new_width, height))
    else:
        new_height = int(width / target_ratio)
        top = (height - new_height) // 2
        image = image.crop((0, top, width, top + new_height))
    image = image.resize(THUMBNAIL_SIZE, Image.LANCZOS)
    image = ImageEnhance.Color(image).enhance(1.15)

    # A gradient scrim keeps the text readable over any footage.
    scrim = Image.new("L", THUMBNAIL_SIZE, 0)
    scrim_draw = ImageDraw.Draw(scrim)
    for y in range(THUMBNAIL_SIZE[1]):
        fraction = y / THUMBNAIL_SIZE[1]
        opacity = int(215 * max(0.0, (fraction - 0.35) / 0.65) ** 1.4)
        scrim_draw.line([(0, y), (THUMBNAIL_SIZE[0], y)], fill=opacity)
    image = Image.composite(Image.new("RGB", THUMBNAIL_SIZE, (0, 0, 0)), image, scrim)

    headline = (text or "").strip().upper()
    if headline:
        draw = ImageDraw.Draw(image)
        lines = textwrap.wrap(headline, width=16) or [headline]
        lines = lines[:3]

        size = 132 if len(lines) == 1 else 108 if len(lines) == 2 else 88
        font = load_font(size)

        # Shrink until the widest line fits inside the safe margins.
        margin = 80
        while size > 40:
            widest = max(draw.textlength(line, font=font) for line in lines)
            if widest <= THUMBNAIL_SIZE[0] - margin * 2:
                break
            size -= 8
            font = load_font(size)

        line_height = int(size * 1.16)
        block_height = line_height * len(lines)
        y = THUMBNAIL_SIZE[1] - margin - block_height

        for line in lines:
            line_width = draw.textlength(line, font=font)
            x = (THUMBNAIL_SIZE[0] - line_width) / 2
            draw.text(
                (x, y),
                line,
                font=font,
                fill=(255, 255, 255),
                stroke_width=max(3, size // 22),
                stroke_fill=(0, 0, 0),
            )
            y += line_height

    destination.parent.mkdir(parents=True, exist_ok=True)
    quality = 92
    while quality >= 60:
        image.save(destination, "JPEG", quality=quality, optimize=True, progressive=True)
        if destination.stat().st_size <= MAX_BYTES:
            break
        quality -= 8
    log.info("Thumbnail written: %s (%.0f KB)", destination.name, destination.stat().st_size / 1024)
    return destination


def build_thumbnail(video: Path, text: str, destination: Path, work_dir: Path) -> Path | None:
    """Best-effort thumbnail. A failure here must never sink the upload."""
    try:
        frame = grab_frame(video, work_dir / "thumb_frame.jpg")
        return compose(frame, text, destination)
    except Exception as exc:  # Pillow and ffmpeg raise a wide variety here.
        log.warning("Thumbnail generation failed (%s); YouTube will pick its own.", exc)
        return None
