"""LTX-2.3 through fal.ai's queue API — the alternate route to the same model.

Useful when the direct LTX API is unavailable, or when billing already runs
through fal. Select it with `video.provider: fal` and a FAL_KEY.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

from ..config import Config
from .base import (
    ClipRequest,
    VideoGenerationError,
    download,
    find_video_url,
    poll_until_done,
    raise_for_api_error,
)

log = logging.getLogger(__name__)

QUEUE_BASE = "https://queue.fal.run"
DEFAULT_ENDPOINT = "fal-ai/ltx-2.3/text-to-video"


class FalLTXProvider:
    name = "fal"

    def __init__(self, config: Config):
        self.config = config
        self.api_key = config.secrets.require("fal_api_key", "FAL_KEY (fal.ai API key)")
        # `video.model` names the LTX model; the fal route needs a full endpoint id.
        self.endpoint = os.environ.get("FAL_ENDPOINT") or DEFAULT_ENDPOINT
        self.job_timeout = float(os.environ.get("LTX_JOB_TIMEOUT_SECONDS", "900"))
        self.generate_audio = bool(config.get("video.generate_audio", False))

        self.client = httpx.Client(
            headers={
                "Authorization": f"Key {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(120.0, connect=20.0),
        )

    def build_payload(self, request: ClipRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": request.prompt,
            "duration": int(round(request.seconds)),
            "resolution": request.resolution,
            "aspect_ratio": request.aspect_ratio,
            "generate_audio": self.generate_audio,
        }
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.seed is not None:
            payload["seed"] = request.seed
        return payload

    def _submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(f"{QUEUE_BASE}/{self.endpoint}", json=payload)
        if response.status_code in (401, 403):
            raise VideoGenerationError(f"fal rejected FAL_KEY (HTTP {response.status_code}).")
        return raise_for_api_error(response, f"fal submit {self.endpoint}")

    def _status(self, request_id: str, status_url: str | None) -> dict[str, Any]:
        url = status_url or f"{QUEUE_BASE}/{self.endpoint}/requests/{request_id}/status"
        return raise_for_api_error(self.client.get(url), "fal status")

    def _result(self, request_id: str, response_url: str | None) -> dict[str, Any]:
        url = response_url or f"{QUEUE_BASE}/{self.endpoint}/requests/{request_id}"
        return raise_for_api_error(self.client.get(url), "fal result")

    def generate(self, request: ClipRequest, destination: Path) -> Path:
        submitted = self._submit(self.build_payload(request))
        request_id = submitted.get("request_id") or submitted.get("requestId")
        if not request_id:
            raise VideoGenerationError(f"fal submit response has no request_id: {submitted}")

        status_url = submitted.get("status_url")
        response_url = submitted.get("response_url")

        poll_until_done(
            lambda: self._status(request_id, status_url),
            timeout_seconds=self.job_timeout,
            label=f"fal request {request_id}",
        )
        final = self._result(request_id, response_url)
        url = find_video_url(final)
        if not url:
            raise VideoGenerationError(f"fal request {request_id} finished without a video URL: {final}")
        return download(self.client, url, destination)

    def probe(self, prompt: str) -> dict[str, Any]:
        request = ClipRequest(
            prompt=prompt,
            seconds=float(self.config.get("video.clip_seconds", 8)),
            aspect_ratio="16:9",
            resolution="480p",
            negative_prompt="",
            seed=None,
        )
        payload = self.build_payload(request)
        submitted = self._submit(payload)
        return {
            "endpoint": self.endpoint,
            "request_body": payload,
            "submit_response": submitted,
        }

    def close(self) -> None:
        self.client.close()
