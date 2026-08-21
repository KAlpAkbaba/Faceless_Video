"""Text-to-video providers."""

from __future__ import annotations

from ..config import Config, ConfigError
from .base import VideoProvider


def build_provider(config: Config) -> VideoProvider:
    """Construct the provider named by video.provider."""
    name = config.get("video.provider", "ltx")
    if name == "ltx":
        from .ltx import LTXProvider

        return LTXProvider(config)
    if name == "fal":
        from .fal_ltx import FalLTXProvider

        return FalLTXProvider(config)
    raise ConfigError(f"Unknown video.provider: {name!r}")


__all__ = ["VideoProvider", "build_provider"]
