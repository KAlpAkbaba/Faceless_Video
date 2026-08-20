"""Rolling history so the ideation step never repeats a topic."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class HistoryEntry:
    slug: str
    title: str
    angle: str
    created_at: str
    video_ids: list[str]


class History:
    """A small append-only JSON ledger of everything the channel has published."""

    def __init__(self, path: Path, keep_last: int = 120):
        self.path = path
        self.keep_last = keep_last
        self.entries: list[HistoryEntry] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # A corrupt ledger must not stop today's video; start a fresh one and
            # keep the broken file around for inspection.
            self.path.rename(self.path.with_suffix(".corrupt.json"))
            return
        for item in raw.get("entries", []):
            self.entries.append(
                HistoryEntry(
                    slug=item.get("slug", ""),
                    title=item.get("title", ""),
                    angle=item.get("angle", ""),
                    created_at=item.get("created_at", ""),
                    video_ids=list(item.get("video_ids", [])),
                )
            )

    def recent_titles(self, limit: int | None = None) -> list[str]:
        limit = limit or self.keep_last
        return [e.title for e in self.entries[-limit:] if e.title]

    def recent_slugs(self, limit: int | None = None) -> set[str]:
        limit = limit or self.keep_last
        return {e.slug for e in self.entries[-limit:] if e.slug}

    def has_slug(self, slug: str) -> bool:
        return slug in self.recent_slugs()

    def add(self, slug: str, title: str, angle: str, video_ids: list[str] | None = None) -> HistoryEntry:
        entry = HistoryEntry(
            slug=slug,
            title=title,
            angle=angle,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            video_ids=list(video_ids or []),
        )
        self.entries.append(entry)
        return entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [asdict(e) for e in self.entries[-self.keep_last * 2 :]]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
