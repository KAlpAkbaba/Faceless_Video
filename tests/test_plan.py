"""The editorial queue, and that production actually uses it."""

import pytest
import yaml

from pipeline.config import Config, ConfigError
from pipeline.plan import EpisodePlan


def write_plan(tmp_path, episodes, order=None):
    path = tmp_path / "episodes.yaml"
    doc = {"episodes": episodes}
    if order is not None:
        doc["publish_order"] = order
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def episode(number, title):
    return {
        "id": number,
        "title": title,
        "characters": ["Benny"],
        "theme": "colors",
        "keyword": "colors for kids",
        "thumbnail": "one big red ball",
        "hook": "Where did the colors go?",
    }


def test_publish_order_is_followed(tmp_path):
    path = write_plan(tmp_path, [episode(1, "One"), episode(2, "Two"), episode(3, "Three")], [3, 1, 2])
    plan = EpisodePlan(path)
    assert plan.next_episode(set()).title == "Three"


def test_published_episodes_are_skipped(tmp_path):
    path = write_plan(tmp_path, [episode(1, "One"), episode(2, "Two")], [1, 2])
    plan = EpisodePlan(path)
    first = plan.next_episode(set())
    assert plan.next_episode({first.slug}).title == "Two"
    assert plan.remaining({first.slug}) == 1


def test_episodes_missing_from_publish_order_still_get_made(tmp_path):
    path = write_plan(tmp_path, [episode(1, "One"), episode(2, "Two")], [2])
    plan = EpisodePlan(path)
    assert [plan.episodes[i].title for i in plan.order] == ["Two", "One"]


def test_exhausted_queue_returns_nothing_so_the_caller_can_invent(tmp_path):
    path = write_plan(tmp_path, [episode(1, "One")], [1])
    plan = EpisodePlan(path)
    assert plan.next_episode({plan.episodes[1].slug}) is None


def test_a_missing_plan_is_not_an_error(tmp_path):
    plan = EpisodePlan(tmp_path / "nothing.yaml")
    assert plan.next_episode(set()) is None


def test_duplicate_ids_are_rejected(tmp_path):
    path = write_plan(tmp_path, [episode(1, "One"), episode(1, "Also one")])
    with pytest.raises(ConfigError, match="Duplicate episode id"):
        EpisodePlan(path)


def test_publish_order_referencing_an_unknown_episode_is_rejected(tmp_path):
    path = write_plan(tmp_path, [episode(1, "One")], [1, 99])
    with pytest.raises(ConfigError, match="unknown episode ids"):
        EpisodePlan(path)


def test_the_writer_actually_uses_the_plan(tmp_path, monkeypatch):
    """The queue existed once without anything calling it.

    A plan nothing reads is worse than no plan: production silently invents
    topics while the file suggests otherwise.
    """
    import anthropic

    from pipeline.writer import Writer

    path = write_plan(tmp_path, [episode(7, "Planned Episode")], [7])
    config = Config(
        {
            "content_mode": "kids",
            "content_plan": str(path),
            "channel": {"name": "T", "characters": "Benny is a boy.", "cast": ["Benny"]},
            "video": {"provider": "ltx", "model": "m"},
            "publish": {"publish_at_local": "19:00", "timezone": "UTC"},
        }
    )

    writer = Writer(config, client=anthropic.Anthropic(api_key="unused"))

    def explode(*args, **kwargs):
        raise AssertionError("pick_topic called the model despite a queued episode")

    monkeypatch.setattr(writer.client.messages, "parse", explode)

    idea = writer.pick_topic([], set())
    assert idea.working_title == "Planned Episode"
    assert writer.planned is not None
    assert writer.planned.characters == ["Benny"]
