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


def test_the_sample_builds_valid_lines_for_every_character():
    """cmd_voices --sample constructs ScriptLine by hand.

    It broke the moment ScriptLine gained a required field, which no test
    noticed because nothing exercised the construction.
    """
    from pipeline.cli import SAMPLE_LINES
    from pipeline.models import ScriptLine

    config = Config.load()
    names = list(config.get("channel.cast", [])) + ["Narrator"]
    assert names

    for index, name in enumerate(names):
        emotion, text = SAMPLE_LINES[index % len(SAMPLE_LINES)]
        line = ScriptLine(speaker=name, text=f"Hello, I am {name}. {text}", emotion=emotion)
        assert line.emotion and line.text


def test_the_sample_covers_several_emotions():
    """One flat sentence repeated cannot show whether emotion carries."""
    from pipeline.cli import SAMPLE_LINES

    emotions = [emotion for emotion, _ in SAMPLE_LINES]
    assert len(set(emotions)) == len(emotions)
    assert len(emotions) >= 5


def test_the_sample_command_runs_end_to_end(tmp_path, monkeypatch):
    """Exercise cmd_voices itself, not just the pieces it is made of.

    Two bugs shipped because nothing called this function: a ScriptLine built
    without a field the model had gained, and a destination that a later edit
    removed. Both are the kind that only a real invocation finds.
    """
    import argparse

    import pipeline.cli as cli
    from pipeline.config import Config

    config = Config.load()
    config.data["voice"]["provider"] = "silent"
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)

    code = cli.cmd_voices(
        config, argparse.Namespace(source=None, cut="longform", sample=True, list_voices=False)
    )

    assert code == 0
    written = tmp_path / "out" / "voices" / "cast-sample.mp3"
    assert written.exists() and written.stat().st_size > 1000


def test_voices_without_a_storyboard_explains_itself(tmp_path, monkeypatch):
    import argparse

    import pipeline.cli as cli
    from pipeline.config import Config

    config = Config.load()
    config.data["voice"]["provider"] = "silent"
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)

    code = cli.cmd_voices(
        config, argparse.Namespace(source=None, cut="longform", sample=False, list_voices=False)
    )
    assert code == 2


def test_the_model_is_one_that_understands_audio_tags():
    """Emotions are sent as [excited]-style tags, which only eleven_v3 reads.

    An older model either ignores the tag or speaks it aloud, and speaking it
    is worse than having no emotion at all.
    """
    config = Config.load()
    assert config.get("voice.elevenlabs_model") == "eleven_v3"


def test_timestamps_are_preferred_but_not_required():
    """Word-accurate captions come from the timestamps endpoint.

    A model that does not offer them must still produce audio rather than
    failing the run, with timings estimated instead.
    """
    from pipeline.voice import ELEVEN_TTS_PATHS

    assert ELEVEN_TTS_PATHS[0] == "/with-timestamps"
    assert "" in ELEVEN_TTS_PATHS
