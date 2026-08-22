"""Choosing the still each shot is animated from.

Image-to-video animates *from a picture*, and that picture becomes the first
frame of the shot. This has a consequence that is easy to miss: a character
sheet on a white background produces a shot of the character on a white
background. Identity references and first frames are different things.

  assets/characters/  who the characters are — the sheets, kept for reference
                      and for regenerating frames. Not sent to the API.
  assets/frames/      what a shot starts as — the characters standing in a
                      real place, lit, framed like the show. These are sent.

Frames are matched to shots by the character names in the filename.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .config import REPO_ROOT, ConfigError

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


@dataclass
class ReferenceLibrary:
    """The frames available, indexed by which characters appear in them."""

    frames: list[Path] = field(default_factory=list)
    by_character: dict[str, list[Path]] = field(default_factory=lambda: defaultdict(list))
    _cursor: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def __post_init__(self) -> None:
        if not isinstance(self.by_character, defaultdict):
            self.by_character = defaultdict(list, self.by_character)

    def pick(self, characters: list[str]) -> Path | None:
        """A frame for these characters, rotating so shots do not all match.

        Prefers a frame naming every character in the shot, then one naming the
        most important of them, then anything at all.
        """
        if not self.frames:
            return None

        names = [_normalise(c) for c in characters if c.strip()]

        if len(names) > 1:
            key = "+".join(sorted(names))
            group = self.by_character.get(key)
            if group:
                return self._rotate(key, group)

        for name in names:
            group = self.by_character.get(name)
            if group:
                return self._rotate(name, group)

        if names:
            log.debug("No frame names %s; using the general pool.", ", ".join(names))
        return self._rotate("__any__", self.frames)

    def _rotate(self, key: str, group: list[Path]) -> Path:
        index = self._cursor[key] % len(group)
        self._cursor[key] += 1
        return group[index]


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def load_library(config) -> ReferenceLibrary:
    """Index the frame directory, warning about the sheets/frames confusion."""
    directory = Path(config.get("video.frame_dir", "assets/frames"))
    if not directory.is_absolute():
        directory = REPO_ROOT / directory

    cast = {_normalise(name) for name in config.get("channel.cast", []) or []}
    if not cast:
        raise ConfigError(
            "channel.cast must list the character names for reference frames to be "
            "matched to shots."
        )

    library = ReferenceLibrary()
    if not directory.exists():
        raise ConfigError(
            f"video.generation is 'image' but {directory} does not exist.\n"
            "Put the first-frame stills there — the characters standing in a real "
            "place, framed like the show. A white-background character sheet used "
            "as a first frame produces a shot on a white background."
        )

    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        library.frames.append(path)

        # benny-lila-kitchen.png indexes under benny, lila and benny+lila.
        # "kitchen" is a place, not a character, so the cast list decides which
        # parts of the name count — otherwise the combined key carries the
        # location too and never matches a shot.
        parts = [_normalise(part) for part in path.stem.split("-") if part.strip()]
        known = [part for part in parts if part in cast]
        if not known:
            log.warning(
                "%s names none of the cast, so every shot falls back to it regardless "
                "of who is in the shot. Rename it after the characters it shows, for "
                "example benny-lila-garden.png.",
                path.name,
            )
        for part in known:
            library.by_character[part].append(path)
        if len(known) > 1:
            library.by_character["+".join(sorted(known))].append(path)

    if not library.frames:
        raise ConfigError(
            f"No images found in {directory}. Name them after the characters in "
            "the shot, for example benny-park.png or benny-lila-kitchen.png."
        )

    log.info(
        "Reference frames: %d files, covering %s",
        len(library.frames),
        ", ".join(sorted(k for k in library.by_character if "+" not in k)) or "nothing named",
    )
    return library
