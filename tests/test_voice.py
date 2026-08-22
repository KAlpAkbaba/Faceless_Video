"""Per-character voices, and the pauses between lines."""

from pipeline.config import Config
from pipeline.voice import voice_for


def make_config(cast=None, **extra):
    data = {
        "voice": {
            "provider": "edge",
            "cast": cast if cast is not None else {},
            "default_voice": "en-US-AnaNeural",
            "default_rate": "+6%",
            "default_pitch": "+0Hz",
            **extra,
        }
    }
    return Config(data)


def test_each_character_gets_its_own_voice():
    config = make_config(
        {
            "Benny": {"voice": "en-US-AnaNeural", "rate": "+14%", "pitch": "+18Hz"},
            "Bobo": {"voice": "en-US-AnaNeural", "rate": "-6%", "pitch": "-12Hz"},
        }
    )
    benny = voice_for(config, "Benny")
    bobo = voice_for(config, "Bobo")

    # Same base voice, but they must not come out sounding identical — that is
    # the whole point of a cast.
    assert benny["voice"] == bobo["voice"]
    assert (benny["rate"], benny["pitch"]) != (bobo["rate"], bobo["pitch"])


def test_an_unlisted_speaker_falls_back_rather_than_failing():
    config = make_config({"Benny": {"voice": "en-US-AnaNeural"}})
    fallback = voice_for(config, "Someone New")
    assert fallback["voice"] == "en-US-AnaNeural"
    assert fallback["rate"] == "+6%"


def test_a_cast_entry_without_a_voice_is_ignored():
    config = make_config({"Benny": {"rate": "+10%"}})
    assert voice_for(config, "Benny")["rate"] == "+6%"


def test_the_children_do_not_share_the_narrator_voice():
    """An adult reading every line is exactly what the dialogue split replaces."""
    config = Config.load()
    narrator = voice_for(config, "Narrator")["voice"]
    for name in ("Benny", "Lila", "Luna"):
        assert voice_for(config, name)["voice"] != narrator


def test_no_two_characters_are_voiced_identically():
    """Six characters that sound the same are one character."""
    config = Config.load()
    settings = [
        tuple(voice_for(config, name)[k] for k in ("voice", "rate", "pitch"))
        for name in config.get("channel.cast", [])
    ]
    assert len(set(settings)) == len(settings)


def test_every_configured_character_is_in_the_cast_list():
    config = Config.load()
    cast = config.get("voice.cast", {})
    for name in config.get("channel.cast", []):
        assert name in cast, f"{name} has no voice; it would fall back to the default"
