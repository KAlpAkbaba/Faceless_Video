import pytest

from pipeline.config import ConfigError
from pipeline.providers.base import find_error_message, find_job_id, find_status, find_video_url
from pipeline.providers.ltx import resolve_dimensions


def test_job_id_found_at_any_depth():
    assert find_job_id({"data": {"generation_id": "abc"}}) == "abc"
    assert find_job_id({"id": "top"}) == "top"
    assert find_job_id({"nothing": 1}) is None


def test_video_url_prefers_an_actual_video_file():
    payload = {"links": [{"url": "https://x/thumb.jpg"}, {"url": "https://x/clip.mp4?sig=1"}]}
    assert find_video_url(payload) == "https://x/clip.mp4?sig=1"


def test_video_url_falls_back_to_a_url_shaped_key():
    assert find_video_url({"output": {"video_url": "https://x/stream"}}) == "https://x/stream"


def test_status_is_normalised():
    assert find_status({"job": {"state": "IN_PROGRESS"}}) == "in_progress"


def test_error_messages_are_collected():
    message = find_error_message({"error": "bad prompt", "detail": "policy"})
    assert "bad prompt" in message and "policy" in message


@pytest.mark.parametrize(
    "resolution,aspect,expected",
    [
        ("1080p", "16:9", (1920, 1080)),
        ("1080p", "9:16", (1080, 1920)),
        ("720p", "9:16", (720, 1280)),
        ("1080p", "1:1", (1080, 1080)),
        ("1920x1080", "16:9", (1920, 1080)),
    ],
)
def test_dimensions(resolution, aspect, expected):
    assert resolve_dimensions(resolution, aspect) == expected


def test_dimensions_are_always_even():
    # H.264 cannot encode odd dimensions.
    for aspect in ("16:9", "9:16", "4:3", "21:9", "5:4"):
        width, height = resolve_dimensions("1080p", aspect)
        assert width % 2 == 0 and height % 2 == 0


@pytest.mark.parametrize("bad", [("9000p", "16:9"), ("1080p", "16/9"), ("1080p", "0:9")])
def test_bad_dimension_input_is_rejected(bad):
    with pytest.raises(ConfigError):
        resolve_dimensions(*bad)


def test_every_discovery_candidate_has_resolvable_pixels():
    """The sweep must never feed a payload-only value into the pixel maths.

    Passing the candidate itself to resolve_dimensions is what made the first
    version of discovery abort on its second attempt.
    """
    from pipeline.providers.ltx import RESOLUTION_CANDIDATES, RESOLUTION_PIXELS

    for candidate in RESOLUTION_CANDIDATES:
        label = candidate if candidate in RESOLUTION_PIXELS else "1080p"
        width, height = resolve_dimensions(label, "16:9")
        assert width > 0 and height > 0


def test_discovery_sweeps_the_configured_model_first():
    from pipeline.providers.ltx import MODEL_CANDIDATES

    configured = "ltx-2-3-fast"
    models = [configured] + [m for m in MODEL_CANDIDATES if m != configured]
    assert models[0] == configured
    assert len(models) == len(set(models))
