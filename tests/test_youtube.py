from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pipeline.youtube import clamp_metadata, next_publish_time, to_rfc3339

ISTANBUL = ZoneInfo("Europe/Istanbul")
NEW_YORK = ZoneInfo("America/New_York")


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def test_slot_later_today_is_used():
    moment = next_publish_time("19:00", ISTANBUL, now=utc("2026-08-20T05:00:00"))
    assert to_rfc3339(moment) == "2026-08-20T16:00:00Z"


def test_slot_already_passed_rolls_to_tomorrow():
    moment = next_publish_time("19:00", ISTANBUL, now=utc("2026-08-20T16:30:00"))
    assert to_rfc3339(moment) == "2026-08-21T16:00:00Z"


def test_slot_too_close_rolls_to_tomorrow():
    # Ten minutes of lead time is not enough for YouTube to schedule reliably.
    moment = next_publish_time("19:00", ISTANBUL, now=utc("2026-08-20T15:50:00"))
    assert to_rfc3339(moment) == "2026-08-21T16:00:00Z"


def test_publish_time_is_always_in_the_future():
    for hour in range(24):
        now = utc(f"2026-08-20T{hour:02d}:00:00")
        assert next_publish_time("19:00", ISTANBUL, now=now) > now


def test_wall_clock_time_survives_a_dst_change():
    # New York leaves DST on 2026-11-01; 18:00 local must stay 18:00 local.
    moment = next_publish_time("18:00", NEW_YORK, now=utc("2026-10-31T23:00:00"))
    assert moment.astimezone(NEW_YORK).hour == 18


def test_titles_are_clamped_and_stripped_of_angle_brackets():
    title, _, _ = clamp_metadata("<b>" + "word " * 40, "d", [])
    assert len(title) <= 100
    assert "<" not in title and ">" not in title


def test_description_is_clamped():
    _, description, _ = clamp_metadata("t", "x" * 9000, [])
    assert len(description) == 5000


def test_tag_block_stays_within_the_api_limit():
    _, _, tags = clamp_metadata("t", "d", [f"tag number {i}" for i in range(200)])
    assert tags
    assert sum(len(t) + (2 if " " in t else 0) + 1 for t in tags) <= 460


def test_empty_title_gets_a_placeholder():
    title, _, _ = clamp_metadata("   ", "d", [])
    assert title == "Untitled"
