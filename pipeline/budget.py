"""Cost estimation and the hard spend ceiling for an unattended daily run."""

from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    """Raised before any paid API call when the run would cost too much."""


# Rough per-run cost of the two Claude calls (ideation + scriptwriting).
# Small next to video generation, but counted so the ceiling means something.
LLM_COST_ESTIMATE_USD = 0.15
# Roughly $0.30 per 1M characters; a 6-minute script is ~5k characters.
ELEVENLABS_COST_PER_1K_CHARS = 0.30


@dataclass
class CostLine:
    label: str
    usd: float
    detail: str


@dataclass
class CostEstimate:
    lines: list[CostLine]

    @property
    def total(self) -> float:
        return round(sum(line.usd for line in self.lines), 4)

    def render(self) -> str:
        width = max((len(line.label) for line in self.lines), default=10)
        rows = [f"  {line.label:<{width}}  ${line.usd:>7.3f}   {line.detail}" for line in self.lines]
        rows.append(f"  {'TOTAL':<{width}}  ${self.total:>7.3f}")
        return "\n".join(rows)


def estimate_run_cost(
    *,
    longform_clips: int,
    shorts_clips: int,
    clip_seconds: float,
    price_per_second: float,
    narration_chars: int = 0,
    voice_provider: str = "edge",
) -> CostEstimate:
    """Estimate what one full run will spend, before spending any of it."""
    lines: list[CostLine] = [CostLine("claude", LLM_COST_ESTIMATE_USD, "ideation + scriptwriting")]

    if longform_clips:
        seconds = longform_clips * clip_seconds
        lines.append(
            CostLine(
                "ltx:longform",
                round(seconds * price_per_second, 4),
                f"{longform_clips} clips x {clip_seconds:g}s @ ${price_per_second}/s",
            )
        )
    if shorts_clips:
        seconds = shorts_clips * clip_seconds
        lines.append(
            CostLine(
                "ltx:shorts",
                round(seconds * price_per_second, 4),
                f"{shorts_clips} clips x {clip_seconds:g}s @ ${price_per_second}/s",
            )
        )

    if voice_provider == "elevenlabs" and narration_chars:
        lines.append(
            CostLine(
                "elevenlabs",
                round(narration_chars / 1000 * ELEVENLABS_COST_PER_1K_CHARS, 4),
                f"{narration_chars} characters",
            )
        )
    else:
        lines.append(CostLine("tts", 0.0, f"{voice_provider} (free)"))

    return CostEstimate(lines)


def enforce_budget(estimate: CostEstimate, ceiling_usd: float) -> None:
    """Abort the run if the estimate is over the configured ceiling."""
    if ceiling_usd <= 0:
        raise BudgetExceeded("budget.max_usd_per_run must be greater than zero.")
    if estimate.total > ceiling_usd:
        raise BudgetExceeded(
            f"Estimated run cost ${estimate.total:.2f} exceeds the ceiling of "
            f"${ceiling_usd:.2f}.\n{estimate.render()}\n"
            "Lower video.max_clips / shorts.max_clips, or raise budget.max_usd_per_run."
        )
