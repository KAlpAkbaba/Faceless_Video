"""Configuration loading: config.yaml for knobs, environment for secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class ConfigError(RuntimeError):
    """Raised when the configuration or the environment is unusable."""


@dataclass(frozen=True)
class Secrets:
    """Every credential the pipeline can use, resolved from the environment."""

    anthropic_api_key: str | None
    ltx_api_key: str | None
    ltx_api_base: str
    fal_api_key: str | None
    elevenlabs_api_key: str | None
    youtube_client_id: str | None
    youtube_client_secret: str | None
    youtube_refresh_token: str | None

    @classmethod
    def from_env(cls) -> "Secrets":
        return cls(
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            ltx_api_key=_env("LTX_API_KEY"),
            # Overridable because LTX has served its API from more than one host.
            ltx_api_base=_env("LTX_API_BASE") or "https://api.ltx.video/v1",
            fal_api_key=_env("FAL_KEY"),
            elevenlabs_api_key=_env("ELEVENLABS_API_KEY"),
            youtube_client_id=_env("YOUTUBE_CLIENT_ID"),
            youtube_client_secret=_env("YOUTUBE_CLIENT_SECRET"),
            youtube_refresh_token=_env("YOUTUBE_REFRESH_TOKEN"),
        )

    def require(self, attr: str, hint: str) -> str:
        value = getattr(self, attr)
        if not value:
            raise ConfigError(f"Missing credential: {hint}")
        return value


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


class Config:
    """Thin typed wrapper over config.yaml with dotted-path access."""

    def __init__(self, data: dict[str, Any], path: Path | None = None):
        self.data = data
        self.path = path
        self.secrets = Secrets.from_env()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
        if not cfg_path.exists():
            raise ConfigError(f"Config file not found: {cfg_path}")
        with cfg_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        config = cls(data, cfg_path)
        config.validate()
        return config

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def require(self, dotted: str) -> Any:
        sentinel = object()
        value = self.get(dotted, sentinel)
        if value is sentinel:
            raise ConfigError(f"Missing required config key: {dotted}")
        return value

    # -- derived helpers ---------------------------------------------------

    @property
    def timezone(self) -> ZoneInfo:
        name = self.get("publish.timezone", "UTC")
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError as exc:  # pragma: no cover - env specific
            raise ConfigError(f"Unknown timezone {name!r}") from exc

    @property
    def history_path(self) -> Path:
        return REPO_ROOT / self.get("state.history_file", "state/history.json")

    def validate(self) -> None:
        """Fail fast on config that would only blow up halfway through a render."""
        for key in ("video.provider", "video.model", "publish.publish_at_local"):
            self.require(key)

        mode = self.get("content_mode", "documentary")
        if mode not in ("documentary", "kids"):
            raise ConfigError(f"content_mode must be 'documentary' or 'kids', got {mode!r}")
        if mode == "documentary" and not self.get("channel.niche"):
            raise ConfigError("channel.niche is required in documentary mode.")
        if mode == "kids" and not self.get("channel.characters"):
            raise ConfigError(
                "channel.characters is required in kids mode — the shot prompts rely on a "
                "fixed cast rather than describing anyone."
            )

        if self.get("video.generation", "text") not in ("text", "image"):
            raise ConfigError("video.generation must be 'text' or 'image'.")

        if not (self.get("longform.enabled") or self.get("shorts.enabled")):
            raise ConfigError("Both longform.enabled and shorts.enabled are false — nothing to make.")

        provider = self.get("video.provider")
        if provider not in ("ltx", "fal"):
            raise ConfigError(f"video.provider must be 'ltx' or 'fal', got {provider!r}")

        voice = self.get("voice.provider")
        if voice not in ("edge", "elevenlabs", "silent"):
            raise ConfigError(f"voice.provider must be 'edge', 'elevenlabs' or 'silent', got {voice!r}")

        strategy = self.get("shorts.clip_strategy", "crop")
        if strategy not in ("crop", "regenerate"):
            raise ConfigError(f"shorts.clip_strategy must be 'crop' or 'regenerate', got {strategy!r}")

        for key in ("publish.publish_at_local", "publish.shorts_publish_at_local"):
            value = self.get(key)
            if value is None:
                continue
            parse_hhmm(str(value), key)

        _ = self.timezone  # raises early on a bad timezone name


def parse_hhmm(value: str, label: str = "time") -> tuple[int, int]:
    """Parse a 'HH:MM' wall-clock string into (hour, minute)."""
    parts = value.split(":")
    if len(parts) != 2:
        raise ConfigError(f"{label} must look like 'HH:MM', got {value!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ConfigError(f"{label} must look like 'HH:MM', got {value!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"{label} out of range: {value!r}")
    return hour, minute
