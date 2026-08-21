"""End-to-end run with fake external services.

Exercises the real orchestration, ffmpeg assembly and file layout while
touching no paid API: the writer and the video provider are stubbed and the
voice provider is the offline 'silent' mode.
"""

import shutil
import subprocess

import pytest

from pipeline.config import Config
from pipeline.models import ScriptPackage, Shot, TopicIdea, VideoScript
from pipeline.run import RunOptions, run

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def make_config(tmp_path):
    return Config(
        {
            "channel": {"name": "Test", "niche": "testing", "language": "en-US"},
            "longform": {"enabled": False},
            "shorts": {
                "enabled": True,
                "target_seconds": 12,
                "aspect_ratio": "9:16",
                "clip_strategy": "regenerate",
                "max_clips": 2,
            },
            "video": {
                "provider": "ltx",
                "model": "ltx-2-3-fast",
                "clip_seconds": 4,
                "max_clips": 2,
                "segment_seconds": 5,
                "transition_seconds": 0.4,
                "fps": 12,
                "concurrency": 2,
            },
            "voice": {"provider": "silent"},
            "captions": {"enabled_shorts": True},
            "music": {"enabled": False},
            "publish": {"publish_at_local": "19:00", "timezone": "UTC"},
            "budget": {"max_usd_per_run": 5.0, "ltx_price_per_second": {"ltx-2-3-fast": 0.04}},
            "state": {"history_file": str(tmp_path / "history.json"), "dedupe_last_n": 10},
        }
    )


class FakeProvider:
    """Renders a colour bar instead of calling a paid video API."""

    name = "fake"

    def __init__(self):
        self.calls = []

    def generate(self, request, destination):
        self.calls.append(request)
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi",
                "-i", f"testsrc2=size=320x180:rate=12:duration={request.seconds}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                str(destination),
            ],
            check=True,
        )
        return destination

    def probe(self, prompt):
        return {}


def script(title, words):
    return VideoScript(
        title=title,
        narration=" ".join(["word"] * words),
        shots=[Shot(beat_label=f"beat {i}", prompt=f"prompt {i}") for i in range(3)],
        description="A description.",
        tags=["tag-one", "tag-two"],
        thumbnail_text="TEST CARD",
    )


class FakeWriter:
    def __init__(self, config, client=None):
        self.config = config

    def pick_topic(self, recent_titles, avoid_slugs):
        return TopicIdea(
            working_title="A Test Topic",
            slug="a-test-topic",
            angle="an angle",
            hook_promise="a promise",
            why_it_travels="it travels",
            key_facts=["fact one"],
            fact_risk="none",
        )

    def write_scripts(self, idea):
        return ScriptPackage(longform=script("Long", 60), shorts=script("Short", 25))


@pytest.fixture
def patched(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr("pipeline.run.Writer", FakeWriter)
    monkeypatch.setattr("pipeline.run.build_provider", lambda config: provider)
    return provider


def test_full_shorts_run_produces_a_playable_file(tmp_path, patched):
    config = make_config(tmp_path)
    report = run(
        config,
        RunOptions(
            only="shorts",
            upload=False,
            work_dir=tmp_path / "work",
            output_dir=tmp_path / "out",
        ),
    )

    assert report.slug == "a-test-topic"
    assert len(report.results) == 1
    result = report.results[0]

    assert result.kind == "shorts"
    assert result.video_path.exists()
    assert result.video_path.stat().st_size > 10_000

    # 25 words at 2.5 words/second is 10s of narration, plus the 1.2s tail.
    assert result.duration == pytest.approx(11.2, abs=0.6)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(result.video_path)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "1080,1920"


def test_run_writes_debug_bundle_and_history(tmp_path, patched):
    config = make_config(tmp_path)
    run(config, RunOptions(only="shorts", upload=False,
                           work_dir=tmp_path / "work", output_dir=tmp_path / "out"))

    assert (tmp_path / "work" / "topic.json").exists()
    assert (tmp_path / "work" / "scripts.json").exists()
    # The topic is recorded so tomorrow's ideation will not repeat it.
    assert "a-test-topic" in (tmp_path / "history.json").read_text()


def test_run_only_generates_the_configured_number_of_clips(tmp_path, patched):
    config = make_config(tmp_path)
    run(config, RunOptions(only="shorts", upload=False,
                           work_dir=tmp_path / "work", output_dir=tmp_path / "out"))
    # shorts.max_clips is 2, even though the script offered 3 shots.
    assert len(patched.calls) == 2
    assert all(call.aspect_ratio == "9:16" for call in patched.calls)


def test_budget_ceiling_stops_the_run_before_any_call(tmp_path, patched):
    from pipeline.budget import BudgetExceeded

    config = make_config(tmp_path)
    config.data["budget"]["max_usd_per_run"] = 0.01
    with pytest.raises(BudgetExceeded):
        run(config, RunOptions(only="shorts", upload=False,
                               work_dir=tmp_path / "work", output_dir=tmp_path / "out"))
    assert patched.calls == []
