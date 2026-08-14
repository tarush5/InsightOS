"""Difference-in-differences: recovery, refusal, and the diagnostics that gate it."""
import numpy as np
import pandas as pd
import pytest

from app.analytics.causal import DiffInDiff, InsufficientDesign


def build_panel(*, effect=0.0, treated_trend=0.0, seed=0, n_units=8,
                n_periods=24, cut=12):
    rng = np.random.default_rng(seed)
    units = [f"u{i}" for i in range(n_units)]
    treated = set(units[:3])
    rows = []
    for unit in units:
        level = rng.normal(100, 8)
        for t in range(n_periods):
            value = level + 0.4 * t + 5 * np.sin(t / 3) + rng.normal(0, 2)
            if unit in treated:
                value += treated_trend * t
                if t >= cut:
                    value += effect
            rows.append({"unit": unit, "period": t, "outcome": value})
    return pd.DataFrame(rows), sorted(treated)


def test_recovers_a_known_effect():
    panel, treated = build_panel(effect=-20.0, seed=1)
    est = DiffInDiff().estimate(panel, treated_units=treated, treatment_period=12)
    assert est.ci_low <= -20.0 <= est.ci_high
    assert est.significant and est.credible


def test_finds_nothing_when_nothing_happened():
    panel, treated = build_panel(effect=0.0, seed=2)
    est = DiffInDiff().estimate(panel, treated_units=treated, treatment_period=12)
    assert not est.significant
    assert "includes zero" in est.interpretation()


def test_pre_existing_divergence_blocks_the_causal_claim():
    """The gap is real; attributing it to the intervention is the error."""
    panel, treated = build_panel(effect=0.0, treated_trend=1.2, seed=3)
    est = DiffInDiff().estimate(panel, treated_units=treated, treatment_period=12)
    trends = next(d for d in est.diagnostics if d.name == "parallel_trends")
    assert not trends.passed
    assert not est.credible
    assert "not usable as a causal estimate" in est.interpretation().lower()


def test_placebo_runs_on_a_fake_pre_period_date():
    panel, treated = build_panel(effect=-20.0, seed=4)
    est = DiffInDiff().estimate(panel, treated_units=treated, treatment_period=12)
    placebo = next(d for d in est.diagnostics if d.name == "placebo")
    assert placebo.passed, placebo.detail


def test_refuses_when_every_unit_is_treated():
    panel, _ = build_panel(effect=-20.0, seed=5)
    with pytest.raises(InsufficientDesign, match="no control group"):
        DiffInDiff().estimate(panel, treated_units=sorted(panel.unit.unique()),
                              treatment_period=12)


def test_refuses_without_enough_pre_periods():
    panel, treated = build_panel(effect=-20.0, seed=6)
    with pytest.raises(InsufficientDesign, match="pre-treatment period"):
        DiffInDiff().estimate(panel[panel.period >= 10], treated_units=treated,
                              treatment_period=12)


def test_unknown_treated_unit_is_rejected():
    panel, _ = build_panel(seed=7)
    with pytest.raises(InsufficientDesign, match="do not appear"):
        DiffInDiff().estimate(panel, treated_units=["atlantis"], treatment_period=12)


def test_control_units_can_be_restricted():
    """A control unit hit by the same shock biases the estimate, so callers must
    be able to exclude one they know about."""
    panel, treated = build_panel(effect=-20.0, seed=8)
    controls = [u for u in panel.unit.unique() if u not in treated][:2]
    est = DiffInDiff().estimate(panel, treated_units=treated,
                                control_units=controls, treatment_period=12)
    assert est.n_control_units == 2
    assert est.n_clusters == len(treated) + 2


def test_few_clusters_are_flagged_in_the_caveats():
    panel, treated = build_panel(effect=-20.0, seed=9, n_units=4)
    est = DiffInDiff().estimate(panel, treated_units=treated[:1],
                                treatment_period=12)
    assert any("clusters" in c for c in est.caveats)


def test_uses_t_not_normal_for_few_clusters():
    """With 4 clusters the critical value is ~3.18, not 1.96. Using the normal
    would make the interval roughly 40% too narrow."""
    panel, treated = build_panel(effect=-20.0, seed=10, n_units=4)
    est = DiffInDiff().estimate(panel, treated_units=treated[:1],
                                treatment_period=12)
    half_width = (est.ci_high - est.ci_low) / 2
    assert half_width / est.std_error > 2.5


def test_estimate_serialises_with_its_caveats_attached():
    panel, treated = build_panel(effect=-20.0, seed=11)
    payload = DiffInDiff().estimate(panel, treated_units=treated,
                                    treatment_period=12).as_dict()
    assert payload["caveats"] and payload["diagnostics"]
    assert "spillover" in " ".join(payload["caveats"]).lower()
