import pytest

from pipeline.config import Config, ConfigError, parse_hhmm


def test_parse_hhmm_accepts_valid_times():
    assert parse_hhmm("00:00") == (0, 0)
    assert parse_hhmm("19:05") == (19, 5)
    assert parse_hhmm("23:59") == (23, 59)


@pytest.mark.parametrize("value", ["24:00", "19:60", "7", "19:5:0", "abc", "-1:00"])
def test_parse_hhmm_rejects_bad_times(value):
    with pytest.raises(ConfigError):
        parse_hhmm(value)


def base_config():
    return {
        "channel": {"niche": "x"},
        "longform": {"enabled": True},
        "shorts": {"enabled": True, "clip_strategy": "crop"},
        "video": {"provider": "ltx", "model": "ltx-2-3-fast"},
        "voice": {"provider": "edge"},
        "publish": {"publish_at_local": "19:00", "timezone": "Europe/Istanbul"},
    }


def test_validate_accepts_a_sane_config():
    Config(base_config()).validate()


def test_validate_rejects_unknown_provider():
    data = base_config()
    data["video"]["provider"] = "sora"
    with pytest.raises(ConfigError, match="video.provider"):
        Config(data).validate()


def test_validate_rejects_a_run_that_builds_nothing():
    data = base_config()
    data["longform"]["enabled"] = False
    data["shorts"]["enabled"] = False
    with pytest.raises(ConfigError, match="nothing to make"):
        Config(data).validate()


def test_validate_rejects_bad_timezone():
    data = base_config()
    data["publish"]["timezone"] = "Mars/Olympus"
    with pytest.raises(ConfigError):
        Config(data).validate()


def test_dotted_get_returns_default_for_missing_path():
    config = Config(base_config())
    assert config.get("video.nope", "fallback") == "fallback"
    assert config.get("nope.nope.nope") is None
