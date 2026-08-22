"""The editorial queue: an episode list that drives production in order.

Without this the pipeline invents a topic every day, which is right for a
documentary channel and wrong for a series. A children's channel is built on a
deliberate catalogue — themes spread so their performance can be compared,
characters rotated so their pull can be measured — and that is a decision made
in advance, not one a model should make each morning.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import REPO_ROOT, ConfigError

log = logging.getLogger(__name__)


@dataclass
class PlannedEpisode:
    id: int
    title: str
    characters: list[str]
    theme: str
    keyword: str
    thumbnail: str
    hook: str

    @property
    def slug(self) -> str:
        from .writer import slugify

        return slugify(f"{self.id:02d}-{self.title}")


class EpisodePlan:
    """Reads the plan and hands out the next episode not yet published."""

    def __init__(self, path: Path):
        self.path = path
        self.episodes: dict[int, PlannedEpisode] = {}
        self.order: list[int] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            log.info("No episode plan at %s; topics will be invented.", self.path)
            return

        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        for item in data.get("episodes", []):
            try:
                episode = PlannedEpisode(
                    id=int(item["id"]),
                    title=str(item["title"]),
                    characters=[str(c) for c in item.get("characters", [])],
                    theme=str(item.get("theme", "")),
                    keyword=str(item.get("keyword", "")),
                    thumbnail=str(item.get("thumbnail", "")),
                    hook=str(item.get("hook", "")),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ConfigError(f"Bad episode entry in {self.path}: {item!r} ({exc})") from exc
            if episode.id in self.episodes:
                raise ConfigError(f"Duplicate episode id {episode.id} in {self.path}")
            self.episodes[episode.id] = episode

        order = [int(i) for i in data.get("publish_order", [])]
        missing = [i for i in order if i not in self.episodes]
        if missing:
            raise ConfigError(f"publish_order references unknown episode ids: {missing}")

        # Anything not named in publish_order still gets made, after the rest.
        self.order = order + [i for i in sorted(self.episodes) if i not in order]
        log.info("Episode plan: %d episodes, %d queued", len(self.episodes), len(self.order))

    def next_episode(self, published_slugs: set[str]) -> PlannedEpisode | None:
        """The first queued episode that has not been published yet."""
        for episode_id in self.order:
            episode = self.episodes[episode_id]
            if episode.slug not in published_slugs:
                return episode
        if self.order:
            log.warning("Every planned episode has been published; falling back to invention.")
        return None

    def remaining(self, published_slugs: set[str]) -> int:
        return sum(1 for i in self.order if self.episodes[i].slug not in published_slugs)


def load_plan(path: str | Path | None = None) -> EpisodePlan:
    return EpisodePlan(Path(path) if path else REPO_ROOT / "content" / "episodes.yaml")
