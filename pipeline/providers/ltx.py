"""LTX (Lightricks) text-to-video provider.

Path and field names are configurable because LTX has shipped more than one
API surface. `python -m pipeline.cli probe` submits one real job and prints the
raw responses so the exact shape can be pinned via environment variables
without touching this file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from ..config import Config, ConfigError
from .base import (
    ClipRequest,
    VideoGenerationError,
    download,
    find_job_id,
    find_video_url,
    poll_until_done,
    raise_for_api_error,
)

log = logging.getLogger(__name__)

# Tried in order on the first call; whichever answers is reused for the rest.
SUBMIT_PATHS = ("/text-to-video", "/generations/text-to-video", "/video/generations", "/generations")
# Verified: /text-to-video works. The image endpoint is not verified yet, so it
# keeps the same try-each-in-turn treatment.
IMAGE_SUBMIT_PATHS = ("/image-to-video", "/generations/image-to-video", "/video/image-to-video")
# Nor is the field the still goes in. Candidates, tried in order.
IMAGE_FIELDS = ("image", "image_url", "input_image", "first_frame", "start_image", "init_image")
STATUS_PATHS = ("/generations/{id}", "/text-to-video/{id}", "/jobs/{id}", "/requests/{id}")

# A rejected request is never generated and never billed, so sweeping the
# combinations costs nothing until one is accepted.
#
# Nothing here is guessed from documentation: the sweep asks the API which
# values it takes. Two error shapes distinguish the cases — "Invalid model X"
# means the id is unknown, while "Resolution X is not supported by model Y"
# means the id is known but that resolution is not on offer for it. A model
# that supports none of them is retired.
MODEL_CANDIDATES: tuple[str, ...] = (
    "ltx-2-5-fast",
    "ltx-2-5-pro",
    "ltx-2-5-lite",
    "ltx-2-5",
    "ltx-2-3-fast",
    "ltx-2-3-pro",
    "ltx-2-fast",
    "ltx-2-pro",
)

# The first six are the labels this module maps to pixels; the rest are other
# spellings the API might expect, since `resolution` is a required field whose
# accepted vocabulary is not published anywhere reachable from here.
RESOLUTION_CANDIDATES: tuple[str | None, ...] = (
    "1080p",
    "720p",
    "1440p",
    "2160p",
    "4k",
    "480p",
    "1080",
    "720",
    "1920x1080",
    "1280x720",
    "FHD",
    "HD",
)

# Tried in order; the first that answers is reported verbatim.
MODEL_LIST_PATHS = ("/models", "/model", "/text-to-video/models")

RESOLUTION_PIXELS = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "2160p": (3840, 2160),
    "4k": (3840, 2160),
}


def looks_like_video(response: httpx.Response) -> bool:
    """Does this response carry the video itself rather than a job record?

    The LTX endpoint answers synchronously with the finished MP4, so there is
    often nothing to poll. Content-Type is the primary signal; the ISO base
    media box header is the fallback for a server that mislabels it.
    """
    content_type = response.headers.get("content-type", "").lower()
    if content_type.startswith(("video/", "application/octet-stream")):
        return True
    return response.content[4:8] == b"ftyp"


class LTXProvider:
    name = "ltx"

    def __init__(self, config: Config):
        self.config = config
        self.api_key = config.secrets.require("ltx_api_key", "LTX_API_KEY (LTX developer console API key)")
        self.base_url = config.secrets.ltx_api_base.rstrip("/")
        self.model = str(config.require("video.model"))
        self.generate_audio = bool(config.get("video.generate_audio", False))
        self.fps = int(config.get("video.fps", 25))
        self.job_timeout = float(os.environ.get("LTX_JOB_TIMEOUT_SECONDS", "900"))

        # Escape hatches: pin the exact paths once `probe` has revealed them.
        self._submit_path: str | None = os.environ.get("LTX_SUBMIT_PATH") or None
        self._status_path: str | None = os.environ.get("LTX_STATUS_PATH") or None
        self._image_path: str | None = os.environ.get("LTX_IMAGE_PATH") or None
        self._image_field: str = os.environ.get("LTX_IMAGE_FIELD") or IMAGE_FIELDS[0]

        # Animating from a still is the only way to keep a character identical
        # across shots, so it uses a different endpoint from plain prompting.
        self.use_reference = str(config.get("video.generation", "text")) == "image"

        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(120.0, connect=20.0),
        )

    # -- request building --------------------------------------------------

    def encode_reference(self, path: Path) -> str:
        """A data URI for the still, since there is nowhere to host a file."""
        import base64
        import mimetypes

        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    def build_payload(self, request: ClipRequest) -> dict[str, Any]:
        """Assemble the generation body.

        Both `resolution` and explicit pixel dimensions are sent: different LTX
        API versions have read one or the other, and an ignored extra field is
        harmless while a missing one is not.
        """
        width, height = resolve_dimensions(request.resolution, request.aspect_ratio)
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": request.prompt,
            "duration": int(round(request.seconds)),
            # Verified against the live API: it wants explicit pixels, and
            # rejects every friendly label ("1080p", "4k", bare "1080").
            "resolution": f"{width}x{height}",
            "aspect_ratio": request.aspect_ratio,
            "width": width,
            "height": height,
            "fps": self.fps,
            "generate_audio": self.generate_audio,
        }
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.reference_image is not None:
            payload[self._image_field] = self.encode_reference(request.reference_image)
        return payload

    # -- HTTP --------------------------------------------------------------

    def _submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        if any(field in payload for field in IMAGE_FIELDS):
            paths = (self._image_path,) if self._image_path else IMAGE_SUBMIT_PATHS
        elif self._submit_path:
            paths = (self._submit_path,)
        else:
            paths = SUBMIT_PATHS
        last_error: Exception | None = None

        for path in paths:
            url = f"{self.base_url}{path}"
            try:
                response = self.client.post(url, json=payload)
            except httpx.HTTPError as exc:
                last_error = exc
                continue

            if response.status_code == 404 and not self._submit_path:
                # Wrong path for this API version — try the next candidate.
                log.debug("LTX submit path %s returned 404, trying next", path)
                last_error = VideoGenerationError(f"404 at {path}")
                continue
            if response.status_code in (401, 403):
                raise VideoGenerationError(
                    f"LTX rejected the API key (HTTP {response.status_code}). "
                    "Check LTX_API_KEY in the Developer Console."
                )

            if any(field in payload for field in IMAGE_FIELDS):
                self._image_path = path
            else:
                self._submit_path = path
            if response.status_code < 400 and looks_like_video(response):
                # The video came back inline; there is no job to track.
                return {"_inline_video": response.content}
            body = raise_for_api_error(response, f"LTX submit {path}")
            return body

        raise VideoGenerationError(
            f"No LTX submit endpoint answered under {self.base_url}. Tried: {', '.join(p for p in paths if p)}. "
            f"Last error: {last_error}. Set LTX_API_BASE / LTX_SUBMIT_PATH after running `probe`."
        ) from last_error

    def _status(self, job_id: str) -> dict[str, Any]:
        paths = (self._status_path,) if self._status_path else STATUS_PATHS
        last_error: Exception | None = None

        for template in paths:
            url = f"{self.base_url}{template.format(id=job_id)}"
            try:
                response = self.client.get(url)
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            if response.status_code == 404 and not self._status_path:
                last_error = VideoGenerationError(f"404 at {template}")
                continue
            body = raise_for_api_error(response, f"LTX status {template}")
            self._status_path = template
            return body

        raise VideoGenerationError(
            f"No LTX status endpoint answered for job {job_id}. Last error: {last_error}. "
            "Set LTX_STATUS_PATH after running `probe`."
        ) from last_error

    # -- public API --------------------------------------------------------

    def generate(self, request: ClipRequest, destination: Path) -> Path:
        payload = self.build_payload(request)
        log.info("LTX submit: %.60s...", request.prompt)
        submitted = self._submit(payload)

        inline = submitted.get("_inline_video")
        if inline:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(inline)
            if destination.stat().st_size < 1024:
                destination.unlink(missing_ok=True)
                raise VideoGenerationError("LTX returned an implausibly small video.")
            log.info("Received %s inline (%.1f MB)", destination.name, len(inline) / 1e6)
            return destination

        # Otherwise it is a job record: find the asset, polling if need be.
        url = find_video_url(submitted)
        if not url:
            job_id = find_job_id(submitted)
            if not job_id:
                raise VideoGenerationError(f"LTX submit response carried neither a job id nor a URL: {submitted}")
            final = poll_until_done(
                lambda: self._status(job_id),
                timeout_seconds=self.job_timeout,
                label=f"LTX job {job_id}",
            )
            url = find_video_url(final)
            if not url:
                raise VideoGenerationError(f"LTX job {job_id} finished without a video URL: {final}")

        return download(self.client, url, destination)

    def try_submit(self, payload: dict[str, Any]) -> tuple[int, str]:  # noqa: D401
        """POST a payload and return (status, body) without raising.

        Used by discovery, where a 4xx is the useful answer rather than a
        failure.
        """
        path = self._submit_path or SUBMIT_PATHS[0]
        response = self.client.post(f"{self.base_url}{path}", json=payload)
        if response.status_code < 400 and looks_like_video(response):
            # Do not paste megabytes of binary into a diagnostic report.
            return response.status_code, f"<{len(response.content)} bytes of video>"
        return response.status_code, response.text[:400]

    def list_models(self) -> dict[str, Any]:
        """Ask the API which models exist, if it will say."""
        for path in MODEL_LIST_PATHS:
            try:
                response = self.client.get(f"{self.base_url}{path}")
            except httpx.HTTPError as exc:
                continue
            if response.status_code == 404:
                continue
            return {"path": path, "status": response.status_code, "body": response.text[:2000]}
        return {"error": f"no listing endpoint answered under {self.base_url}"}

    def discover(self, prompt: str, *, seconds: float) -> dict[str, Any]:
        """Ask the API what it accepts, rather than guessing from docs."""
        catalogue = self.list_models()
        log.info("model listing: %s", str(catalogue)[:300])

        attempts: list[dict[str, Any]] = []
        accepted: dict[str, Any] | None = None
        known_models: list[str] = []

        # The configured model first; the alternates only if it never works.
        models = [self.model] + [m for m in MODEL_CANDIDATES if m != self.model]

        for model in models:
            for candidate in RESOLUTION_CANDIDATES:
                # The pixel maths needs a label it knows; the candidate only
                # ever reaches the payload. Conflating the two is what broke
                # the first version of this sweep.
                label = candidate if candidate in RESOLUTION_PIXELS else "1080p"
                request = ClipRequest(
                    prompt=prompt,
                    seconds=seconds,
                    aspect_ratio="16:9",
                    resolution=label,
                    negative_prompt="",
                    seed=None,
                )
                payload = self.build_payload(request)
                payload["model"] = model
                payload["resolution"] = candidate

                try:
                    status, body = self.try_submit(payload)
                except httpx.HTTPError as exc:
                    attempts.append({"model": model, "resolution": candidate, "error": str(exc)})
                    continue

                attempts.append(
                    {"model": model, "resolution": candidate, "status": status, "body": body}
                )
                log.info("model=%-14s resolution=%-10s -> HTTP %s", model, candidate, status)

                if status < 400:
                    accepted = {"model": model, "resolution": candidate, "response": body}
                    log.info("ACCEPTED: model=%s resolution=%s", model, candidate)
                    break

                if "Invalid model" in body or "invalid model" in body:
                    # The id itself is unknown, so the other resolutions would
                    # fail for the same reason. Move on.
                    log.info("model=%-14s is not a known model id, skipping", model)
                    break

                # Reaching a resolution complaint means the id is real.
                if model not in known_models:
                    known_models.append(model)
            if accepted:
                break

        if not accepted:
            log.error(
                "Nothing accepted. Model ids the API recognises: %s",
                ", ".join(known_models) or "none",
            )

        return {
            "base_url": self.base_url,
            "submit_path": self._submit_path or SUBMIT_PATHS[0],
            "configured_model": self.model,
            "model_listing": catalogue,
            "recognised_models": known_models,
            "accepted": accepted,
            "attempts": attempts,
        }

    def discover_image_to_video(
        self, prompt: str, *, seconds: float, reference: Path
    ) -> dict[str, Any]:
        """Find the image endpoint and the field the still belongs in.

        Same reasoning as the text sweep: a rejected request is not generated
        and not billed, so trying every combination costs nothing until one
        works.
        """
        attempts: list[dict[str, Any]] = []
        accepted: dict[str, Any] | None = None
        encoded = self.encode_reference(reference)

        for path in IMAGE_SUBMIT_PATHS:
            for field_name in IMAGE_FIELDS:
                request = ClipRequest(
                    prompt=prompt,
                    seconds=seconds,
                    aspect_ratio="16:9",
                    resolution="1080p",
                    negative_prompt="",
                    seed=None,
                )
                payload = self.build_payload(request)
                payload.pop(self._image_field, None)
                payload[field_name] = encoded

                try:
                    response = self.client.post(f"{self.base_url}{path}", json=payload)
                except httpx.HTTPError as exc:
                    attempts.append({"path": path, "field": field_name, "error": str(exc)})
                    continue

                if response.status_code < 400 and looks_like_video(response):
                    body = f"<{len(response.content)} bytes of video>"
                else:
                    body = response.text[:300]
                attempts.append(
                    {"path": path, "field": field_name, "status": response.status_code, "body": body}
                )
                log.info("path=%-28s field=%-12s -> HTTP %s", path, field_name, response.status_code)

                if response.status_code < 400:
                    accepted = {"path": path, "field": field_name}
                    log.info("ACCEPTED: LTX_IMAGE_PATH=%s LTX_IMAGE_FIELD=%s", path, field_name)
                    break
                if response.status_code == 404:
                    # Wrong endpoint; the field makes no difference here.
                    break
            if accepted:
                break

        if not accepted:
            log.error("No image-to-video endpoint and field combination was accepted.")
        return {"base_url": self.base_url, "accepted": accepted, "attempts": attempts}

    def probe(self, prompt: str, *, resolution: str, seconds: float) -> dict[str, Any]:
        """Submit one job and report exactly what came back.

        The resolution has to be one the model actually accepts, and the
        accepted spelling is not obvious, so it comes from the caller rather
        than being pinned here. Use `probe --discover` to find it.
        """
        request = ClipRequest(
            prompt=prompt,
            seconds=seconds,
            aspect_ratio="16:9",
            resolution=resolution,
            negative_prompt="",
            seed=None,
        )
        payload = self.build_payload(request)
        submitted = self._submit(payload)
        inline = submitted.pop("_inline_video", None)
        result: dict[str, Any] = {
            "base_url": self.base_url,
            "submit_path": self._submit_path,
            "request_body": payload,
            "submit_response": (
                f"<{len(inline)} bytes of video returned inline>" if inline else submitted
            ),
        }
        if inline:
            return result
        job_id = find_job_id(submitted)
        if job_id and not find_video_url(submitted):
            try:
                result["first_status_response"] = self._status(job_id)
                result["status_path"] = self._status_path
            except VideoGenerationError as exc:
                result["status_error"] = str(exc)
        return result

    def close(self) -> None:
        self.client.close()


def resolve_dimensions(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    """Turn ('1080p', '9:16') into pixel dimensions, keeping the long edge."""
    key = str(resolution).strip().lower()
    if "x" in key:
        width_text, _, height_text = key.partition("x")
        try:
            return int(width_text), int(height_text)
        except ValueError as exc:
            raise ConfigError(f"Unparseable resolution: {resolution!r}") from exc

    if key not in RESOLUTION_PIXELS:
        raise ConfigError(f"Unknown resolution {resolution!r}; use one of {sorted(RESOLUTION_PIXELS)}")
    long_edge, short_edge = RESOLUTION_PIXELS[key]

    try:
        left, _, right = str(aspect_ratio).partition(":")
        num, den = int(left), int(right)
    except ValueError as exc:
        raise ConfigError(f"Unparseable aspect_ratio: {aspect_ratio!r}") from exc
    if num <= 0 or den <= 0:
        raise ConfigError(f"Unparseable aspect_ratio: {aspect_ratio!r}")

    if num == den:  # square: the label describes both edges
        width = height = short_edge
    elif num > den:  # landscape: the label describes the width
        width, height = long_edge, int(round(long_edge * den / num))
    else:  # portrait: the label's long edge becomes the height
        height, width = long_edge, int(round(long_edge * num / den))
    return even(width), even(height)


def even(value: int) -> int:
    """H.264 needs even dimensions."""
    return value if value % 2 == 0 else value + 1
