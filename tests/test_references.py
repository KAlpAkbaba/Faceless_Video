"""Matching shots to the still they are animated from."""

import pytest

from pipeline.config import Config, ConfigError
from pipeline.references import load_library


CAST = ["Benny", "Lila", "Milo", "Pip", "Luna", "Bobo"]


def make_config(tmp_path, frames):
    directory = tmp_path / "frames"
    directory.mkdir()
    for name in frames:
        (directory / name).write_bytes(b"not really a png")
    return Config(
        {
            "channel": {"cast": CAST},
            "video": {"frame_dir": str(directory)},
        }
    )


def test_a_place_in_the_filename_is_not_read_as_a_character(tmp_path):
    """benny-lila-playroom must match a Benny+Lila shot.

    Treating every hyphenated part as a name puts the location in the combined
    key, so the two-character lookup never matches anything.
    """
    config = make_config(tmp_path, ["benny-park.png", "benny-lila-playroom.png"])
    library = load_library(config)
    assert library.pick(["Benny", "Lila"]).name == "benny-lila-playroom.png"


def test_falls_back_from_the_pair_to_the_lead_character(tmp_path):
    config = make_config(tmp_path, ["benny-park.png", "milo-garden.png"])
    library = load_library(config)
    # No frame has both, so the first-named character decides.
    assert library.pick(["Benny", "Luna"]).name == "benny-park.png"


def test_frames_rotate_so_shots_do_not_all_start_alike(tmp_path):
    config = make_config(tmp_path, ["benny-park.png", "benny-home.png", "benny-shop.png"])
    library = load_library(config)
    picked = [library.pick(["Benny"]).name for _ in range(6)]
    assert len(set(picked[:3])) == 3
    assert picked[:3] == picked[3:]


def test_unknown_character_still_gets_a_frame(tmp_path):
    config = make_config(tmp_path, ["benny-park.png"])
    library = load_library(config)
    assert library.pick(["Nobody"]) is not None
    assert library.pick([]) is not None


def test_missing_directory_explains_the_sheets_versus_frames_distinction(tmp_path):
    config = Config({"channel": {"cast": CAST}, "video": {"frame_dir": str(tmp_path / "nope")}})
    with pytest.raises(ConfigError, match="white-background character sheet"):
        load_library(config)


def test_empty_directory_is_an_error(tmp_path):
    config = make_config(tmp_path, [])
    with pytest.raises(ConfigError, match="No images found"):
        load_library(config)


def test_cast_is_required(tmp_path):
    directory = tmp_path / "frames"
    directory.mkdir()
    (directory / "benny.png").write_bytes(b"x")
    config = Config({"channel": {}, "video": {"frame_dir": str(directory)}})
    with pytest.raises(ConfigError, match="channel.cast"):
        load_library(config)
