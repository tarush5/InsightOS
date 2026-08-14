"""Scenario simulation: exact arithmetic and honest framing."""
import pytest

from app.analytics.simulation import (Lever, LeverBasis, ScenarioSimulator,
                                      UnknownSegment)

BASE = {"South": 10_000.0, "North": 25_000.0, "East": 18_000.0, "West": 12_000.0}


def test_immediate_lever_is_exact():
    result = ScenarioSimulator().simulate(BASE, [Lever("South", 0.5)],
                                          horizon_days=10)
    assert result.delta == pytest.approx(10_000 * 0.5 * 10)


def test_ramp_delivers_less_than_the_full_effect():
    sim = ScenarioSimulator()
    instant = sim.simulate(BASE, [Lever("South", 0.4)], horizon_days=90).delta
    ramped = sim.simulate(BASE, [Lever("South", 0.4, ramp_days=60)],
                          horizon_days=90).delta
    assert 0 < ramped < instant


def test_levers_on_one_segment_compound_rather_than_add():
    """Two 50% cuts must leave 25%, not zero."""
    result = ScenarioSimulator().simulate(
        {"A": 100.0}, [Lever("A", -0.5), Lever("A", -0.5)], horizon_days=1)
    assert result.scenario_total == pytest.approx(25.0)


def test_break_even_inverts_the_simulation():
    sim = ScenarioSimulator()
    required = sim.break_even(BASE, "South", target_delta=270_000, horizon_days=90)
    achieved = sim.simulate(BASE, [Lever("South", required)], horizon_days=90).delta
    assert achieved == pytest.approx(270_000)


def test_unknown_segment_names_the_known_ones():
    with pytest.raises(UnknownSegment, match="South"):
        ScenarioSimulator().simulate(BASE, [Lever("Atlantis", 0.1)],
                                     horizon_days=30)


def test_asserted_levers_are_labelled_as_such():
    """The output must not launder a guess into an estimate."""
    result = ScenarioSimulator().simulate(BASE, [Lever("South", 0.4)],
                                          horizon_days=90)
    assumptions = " ".join(result.assumptions).lower()
    assert "asserted, not estimated" in assumptions


def test_evidence_backed_levers_are_not_labelled_asserted():
    result = ScenarioSimulator().simulate(
        BASE, [Lever("South", 0.4, LeverBasis.HISTORICAL,
                     rationale="matches the 2024 recovery")], horizon_days=90)
    assert "asserted, not estimated" not in " ".join(result.assumptions)


def test_unaddressed_segments_are_named():
    result = ScenarioSimulator().simulate(BASE, [Lever("South", 0.4)],
                                          horizon_days=30)
    assert set(result.unaddressed_segments) == {"North", "East", "West"}


def test_sensitivity_ranks_the_assumption_that_matters():
    result = ScenarioSimulator().simulate(
        BASE, [Lever("South", 0.1), Lever("North", 0.4)], horizon_days=90)
    assert result.sensitivity[0].segment == "North"
    assert result.sensitivity[0].swing > result.sensitivity[1].swing


def test_missing_interval_is_disclosed():
    result = ScenarioSimulator().simulate(BASE, [Lever("South", 0.4)],
                                          horizon_days=30)
    assert any("no stated uncertainty" in a for a in result.assumptions)
    assert result.interval_low is None


def test_supplied_interval_is_carried_through():
    result = ScenarioSimulator().simulate(BASE, [Lever("South", 0.4)],
                                          horizon_days=30,
                                          baseline_interval_pct=0.12)
    assert result.interval_low < result.scenario_total < result.interval_high


def test_lever_shares_sum_to_the_total_change():
    result = ScenarioSimulator().simulate(
        BASE, [Lever("South", 0.4), Lever("West", -0.2)], horizon_days=45)
    assert sum(o.delta for o in result.per_lever) == pytest.approx(result.delta)


def test_empty_baseline_is_rejected():
    with pytest.raises(ValueError):
        ScenarioSimulator().simulate({}, [Lever("South", 0.1)], horizon_days=30)
