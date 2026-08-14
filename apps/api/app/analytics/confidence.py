"""
Confidence decomposition (spec section 31).

A single blended "94%" is close to meaningless, so the score is built from four
independently measured components and the weakest one is surfaced by name. None
of these numbers come from a language model -- each is derived from something
that was measured during the investigation.

    data         completeness, freshness, sample size of the queried result
    statistical  p-value and effect size of the headline change
    model        backtest accuracy of any model whose output is being relied on
    reasoning    fraction of critic checks that passed

The overall score is the weighted geometric mean, not the arithmetic mean: one
component near zero must drag the whole score down rather than being averaged
away by three healthy ones.
"""
from __future__ import annotations

from dataclasses import dataclass

_WEIGHTS = {"data": 0.30, "statistical": 0.30, "model": 0.20, "reasoning": 0.20}


@dataclass(slots=True)
class ConfidenceBreakdown:
    data: float
    statistical: float
    model: float
    reasoning: float
    overall: float
    limiting_factor: str
    rationale: list[str]

    @property
    def label(self) -> str:
        if self.overall >= 0.80:
            return "high"
        if self.overall >= 0.60:
            return "moderate"
        if self.overall >= 0.40:
            return "low"
        return "insufficient"

    def as_dict(self) -> dict:
        return {
            "data": round(self.data, 4),
            "statistical": round(self.statistical, 4),
            "model": round(self.model, 4),
            "reasoning": round(self.reasoning, 4),
            "overall": round(self.overall, 4),
            "label": self.label,
            "limiting_factor": self.limiting_factor,
            "rationale": self.rationale,
        }


def _clamp(x: float) -> float:
    return max(0.01, min(1.0, x))


def score_data(
    *, quality_score: float, row_count: int, freshness_days: float, expected_rows: int = 1000
) -> tuple[float, str]:
    quality = _clamp(quality_score / 100.0)
    volume = _clamp(min(1.0, row_count / max(1, expected_rows)) ** 0.5)
    freshness = _clamp(1.0 - min(1.0, max(0.0, freshness_days - 1) / 30.0))
    score = _clamp(quality * 0.5 + volume * 0.25 + freshness * 0.25)
    note = (f"Quality {quality_score:.0f}/100 on {row_count:,} rows, "
            f"{freshness_days:.0f} day(s) old.")
    return score, note


def score_statistical(*, p_value: float | None, effect_size: float | None) -> tuple[float, str]:
    if p_value is None:
        return 0.35, "No significance test was applicable to this change."
    # Map p to confidence: p=0.001 -> ~0.97, p=0.05 -> ~0.70, p=0.5 -> ~0.20
    import math
    conf = _clamp(1.0 - math.exp(-0.45 * math.log10(1.0 / max(p_value, 1e-9)) - 0.05))
    if effect_size is not None and abs(effect_size) < 0.2:
        conf *= 0.6
        return conf, (f"p={p_value:.4f} but effect size is negligible "
                      f"(d={effect_size:.2f}); statistically detectable, not meaningful.")
    return conf, f"p={p_value:.4f}, effect size d={effect_size:.2f}." if effect_size is not None \
        else f"p={p_value:.4f}."


def score_model(*, mape: float | None, beats_baseline: bool, folds: int) -> tuple[float, str]:
    if mape is None:
        base = 0.5 if beats_baseline else 0.35
        return base, "Model accuracy could not be measured on held-out folds."
    accuracy = _clamp(1.0 - min(1.0, mape / 0.5))
    if not beats_baseline:
        accuracy *= 0.65
    if folds < 3:
        accuracy *= 0.85
    note = (f"Backtest MAPE {mape:.1%} over {folds} rolling fold(s); "
            f"{'beats' if beats_baseline else 'does not beat'} the seasonal-naive baseline.")
    return accuracy, note


def score_reasoning(*, checks_passed: int, checks_total: int, blocking_failures: int) -> tuple[float, str]:
    if checks_total == 0:
        return 0.4, "No verification checks were run."
    ratio = checks_passed / checks_total
    score = _clamp(ratio ** 1.5)
    if blocking_failures:
        score = min(score, 0.25)
    return score, f"{checks_passed}/{checks_total} critic checks passed, {blocking_failures} blocking."


def combine(data: tuple, statistical: tuple, model: tuple, reasoning: tuple) -> ConfidenceBreakdown:
    parts = {"data": data, "statistical": statistical, "model": model, "reasoning": reasoning}
    scores = {k: _clamp(v[0]) for k, v in parts.items()}
    overall = 1.0
    for key, weight in _WEIGHTS.items():
        overall *= scores[key] ** weight
    limiting = min(scores, key=scores.get)
    return ConfidenceBreakdown(
        data=scores["data"], statistical=scores["statistical"],
        model=scores["model"], reasoning=scores["reasoning"],
        overall=overall, limiting_factor=limiting,
        rationale=[f"{k}: {v[1]}" for k, v in parts.items()],
    )
