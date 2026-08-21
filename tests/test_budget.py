import pytest

from pipeline.budget import BudgetExceeded, enforce_budget, estimate_run_cost


def test_estimate_counts_clip_seconds():
    estimate = estimate_run_cost(
        longform_clips=12, shorts_clips=6, clip_seconds=8, price_per_second=0.04
    )
    # 18 clips * 8s * $0.04 = $5.76, plus the flat Claude estimate.
    assert estimate.total == pytest.approx(5.76 + 0.15, abs=0.001)


def test_free_tts_adds_nothing():
    estimate = estimate_run_cost(
        longform_clips=1, shorts_clips=0, clip_seconds=8, price_per_second=0.04,
        narration_chars=5000, voice_provider="edge",
    )
    assert estimate.total == pytest.approx(0.32 + 0.15, abs=0.001)


def test_elevenlabs_is_charged_per_character():
    estimate = estimate_run_cost(
        longform_clips=0, shorts_clips=0, clip_seconds=8, price_per_second=0.04,
        narration_chars=10_000, voice_provider="elevenlabs",
    )
    assert estimate.total == pytest.approx(0.15 + 3.0, abs=0.001)


def test_enforce_budget_blocks_an_expensive_run():
    estimate = estimate_run_cost(
        longform_clips=40, shorts_clips=0, clip_seconds=8, price_per_second=0.06
    )
    with pytest.raises(BudgetExceeded, match="exceeds the ceiling"):
        enforce_budget(estimate, 5.0)


def test_enforce_budget_rejects_a_zero_ceiling():
    estimate = estimate_run_cost(longform_clips=1, shorts_clips=0, clip_seconds=8, price_per_second=0.04)
    with pytest.raises(BudgetExceeded):
        enforce_budget(estimate, 0)
