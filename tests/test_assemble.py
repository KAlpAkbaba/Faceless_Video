import pytest

from pipeline.assemble import escape_filter_path, plan_segments, timeline_duration
from pathlib import Path


def test_timeline_covers_the_narration():
    segments = plan_segments(
        361.2, 12, segment_seconds=11, transition_seconds=0.5, clip_seconds=8
    )
    assert timeline_duration(segments, 0.5) >= 361.2


def test_a_clip_never_follows_itself():
    segments = plan_segments(300, 5, segment_seconds=10, transition_seconds=0.5, clip_seconds=8)
    for current, following in zip(segments, segments[1:]):
        assert current.clip_index != following.clip_index


def test_repeats_enter_at_different_offsets():
    segments = plan_segments(300, 3, segment_seconds=10, transition_seconds=0.5, clip_seconds=8)
    offsets = [s.source_offset for s in segments if s.clip_index == 0]
    assert len(offsets) > 1
    assert len(set(offsets)) > 1


def test_single_clip_still_plans():
    segments = plan_segments(30, 1, segment_seconds=11, transition_seconds=0.5, clip_seconds=8)
    assert all(s.clip_index == 0 for s in segments)
    assert timeline_duration(segments, 0.5) >= 30


def test_short_narration_yields_at_least_one_segment():
    segments = plan_segments(3, 4, segment_seconds=11, transition_seconds=0.5, clip_seconds=8)
    assert len(segments) == 1


@pytest.mark.parametrize("bad", [(0, 5), (100, 0)])
def test_plan_rejects_impossible_input(bad):
    duration, clips = bad
    with pytest.raises(ValueError):
        plan_segments(duration, clips, segment_seconds=11, transition_seconds=0.5, clip_seconds=8)


def test_filter_paths_are_escaped():
    escaped = escape_filter_path(Path("/tmp/a:b/c,d/captions.ass"))
    assert r"\:" in escaped and r"\," in escaped
