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


class _FakeResponse:
    """Minimal stand-in for httpx.Response, for the binary-detection tests."""

    def __init__(self, content: bytes, content_type: str = ""):
        self.content = content
        self.headers = {"content-type": content_type} if content_type else {}


def test_inline_video_is_detected_by_content_type():
    from pipeline.providers.ltx import looks_like_video

    assert looks_like_video(_FakeResponse(b"", "video/mp4"))
    assert looks_like_video(_FakeResponse(b"", "application/octet-stream"))


def test_inline_video_is_detected_by_iso_header_when_mislabelled():
    # The live API answers with the MP4 itself; a wrong Content-Type must not
    # send the bytes down the JSON path, where they parse as nothing.
    from pipeline.providers.ltx import looks_like_video

    mp4 = b"\x00\x00\x00 ftypisom\x00\x00\x02\x00isomiso2avc1mp41"
    assert looks_like_video(_FakeResponse(mp4))
    assert looks_like_video(_FakeResponse(mp4, "text/plain"))


def test_json_response_is_not_mistaken_for_video():
    from pipeline.providers.ltx import looks_like_video

    assert not looks_like_video(_FakeResponse(b'{"id": "abc"}', "application/json"))
    assert not looks_like_video(_FakeResponse(b'{"error": "bad"}'))


def test_payload_sends_explicit_pixels_not_a_label():
    """The API rejects '1080p', '4k' and bare '1080'; only WIDTHxHEIGHT works."""
    import os

    from pipeline.config import Config
    from pipeline.providers.base import ClipRequest
    from pipeline.providers.ltx import LTXProvider

    os.environ.setdefault("LTX_API_KEY", "test-key")
    config = Config.load()
    provider = LTXProvider(config)

    landscape = provider.build_payload(
        ClipRequest(prompt="x", seconds=8, aspect_ratio="16:9",
                    resolution="1080p", negative_prompt="", seed=1)
    )
    assert landscape["resolution"] == "1920x1080"

    portrait = provider.build_payload(
        ClipRequest(prompt="x", seconds=8, aspect_ratio="9:16",
                    resolution="1080p", negative_prompt="", seed=1)
    )
    assert portrait["resolution"] == "1080x1920"
