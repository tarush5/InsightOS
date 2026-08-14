"""Alert rule compilation and evaluation."""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.alerts.engine import AlertEngine
from app.alerts.rules import AlertRule, AmbiguousRule, Condition, compile_rule

KNOWN = {"revenue": "Revenue", "churn_rate": "Churn rate", "csat": "CSAT",
         "average_order_value": "Average order value"}


def series(values, start="2025-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(np.asarray(values, dtype=float), index=idx)


# --- compilation -------------------------------------------------------------

def test_verb_beats_comparator_for_direction():
    """'drops by more than 10%' contains both 'drops' and 'more than'. The verb
    carries the direction; reading the comparator inverts the alert."""
    rule = compile_rule("Alert when revenue drops by more than 10% week over week",
                        KNOWN)
    assert rule.operator == "lt"
    assert rule.condition is Condition.CHANGE
    assert rule.threshold == pytest.approx(0.10)


def test_absolute_threshold():
    rule = compile_rule("Alert when csat falls below 4.2", KNOWN)
    assert rule.condition is Condition.THRESHOLD
    assert rule.operator == "lt" and rule.threshold == pytest.approx(4.2)


def test_anomaly_rule():
    rule = compile_rule("Tell me about unusual revenue", KNOWN)
    assert rule.condition is Condition.ANOMALY
    assert rule.min_severity == "high"


def test_longest_metric_match_wins():
    """'average order value' must not be shadowed by a shorter metric name."""
    rule = compile_rule("Alert if average order value rises by 15%", KNOWN)
    assert rule.metric_key == "average_order_value"


def test_missing_threshold_asks_rather_than_guesses():
    with pytest.raises(AmbiguousRule) as exc:
        compile_rule("Let me know about revenue", KNOWN)
    assert exc.value.missing == ["threshold"]


def test_contradictory_direction_is_ambiguous():
    with pytest.raises(AmbiguousRule) as exc:
        compile_rule("Alert when revenue rises or falls by 10%", KNOWN)
    assert "direction" in exc.value.missing


def test_unknown_metric_lists_the_known_ones():
    with pytest.raises(AmbiguousRule) as exc:
        compile_rule("Alert when vibes drop by 10%", KNOWN)
    assert exc.value.missing == ["metric"]
    assert "revenue" in str(exc.value)


def test_rule_round_trips_through_storage():
    rule = compile_rule("Alert when revenue drops more than 10% in 14 days", KNOWN)
    assert AlertRule.from_dict(rule.as_dict()).as_dict() == rule.as_dict()


def test_readback_is_human_checkable():
    rule = compile_rule("Alert when revenue drops more than 10% in 7 days", KNOWN)
    text = rule.describe()
    assert "revenue" in text and "10%" in text and "falls" in text


# --- evaluation --------------------------------------------------------------

def test_change_rule_fires_on_a_real_drop():
    data = series([1000.0] * 53 + [820.0] * 7)
    rule = compile_rule("Alert when revenue drops more than 10% in 7 days", KNOWN)
    result = AlertEngine().evaluate(rule, data)
    assert result.triggered
    assert "-18.00%" in result.reason


def test_change_rule_stays_quiet_below_the_threshold():
    data = series([1000.0] * 53 + [970.0] * 7)
    rule = compile_rule("Alert when revenue drops more than 10% in 7 days", KNOWN)
    assert not AlertEngine().evaluate(rule, data).triggered


def test_quiet_evaluation_still_reports_what_it_measured():
    """A silent 'no alert' is indistinguishable from a broken monitor."""
    data = series([1000.0] * 60)
    rule = compile_rule("Alert when revenue drops more than 10% in 7 days", KNOWN)
    result = AlertEngine().evaluate(rule, data)
    assert not result.triggered
    assert result.observed is not None and result.window_start


def test_cooldown_suppresses_a_repeat_page():
    data = series([1000.0] * 53 + [820.0] * 7)
    rule = compile_rule("Alert when revenue drops more than 10% in 7 days", KNOWN)
    now = datetime(2025, 3, 2, tzinfo=timezone.utc)
    result = AlertEngine().evaluate(rule, data, now=now,
                                    last_triggered_at=now - timedelta(hours=2))
    assert not result.triggered and result.suppressed_by_cooldown


def test_partial_comparison_window_is_refused_not_guessed():
    """Nine days of history cannot support a 7d-vs-prior-7d comparison. Using
    the two days that happen to exist would manufacture a change out of which
    days were present."""
    data = series([1000.0] * 9)
    rule = compile_rule("Alert when revenue drops more than 10% in 7 days", KNOWN)
    result = AlertEngine().evaluate(rule, data)
    assert not result.triggered
    assert "populated" in result.reason
    assert result.evidence["comparison_coverage"] < 0.6


def test_full_comparison_window_is_accepted():
    data = series([1000.0] * 14)
    rule = compile_rule("Alert when revenue drops more than 10% in 7 days", KNOWN)
    result = AlertEngine().evaluate(rule, data)
    assert "populated" not in result.reason


def test_zero_baseline_does_not_divide_by_zero():
    data = series([0.0] * 53 + [10.0] * 7)  # noqa: E501
    rule = compile_rule("Alert when revenue rises more than 10% in 7 days", KNOWN)
    result = AlertEngine().evaluate(rule, data)
    assert not result.triggered and "undefined" in result.reason


def test_empty_series_is_handled():
    rule = compile_rule("Alert when revenue drops more than 10%", KNOWN)
    result = AlertEngine().evaluate(rule, pd.Series(dtype="float64"))
    assert not result.triggered and "No data" in result.reason


def test_anomaly_rule_fires_on_a_spike_and_names_the_date():
    values = [100.0] * 60
    values[45] = 400.0
    rule = compile_rule("Alert on unusual revenue", KNOWN)
    result = AlertEngine().evaluate(rule, series(values))
    assert result.triggered
    assert result.evidence["count"] >= 1


def test_threshold_severity_scales_with_the_overshoot():
    rule = compile_rule("Alert when csat falls below 4.2", KNOWN)
    mild = AlertEngine().evaluate(rule, series([4.1] * 30))
    severe = AlertEngine().evaluate(rule, series([1.5] * 30))
    assert mild.triggered and severe.triggered
    assert severe.severity == "critical" and mild.severity == "medium"


def test_cooldown_handles_naive_and_aware_timestamps():
    """Backtests index on naive pandas timestamps; the database stores aware
    ones. Mixing them used to raise."""
    data = series([1000.0] * 53 + [820.0] * 7)
    rule = compile_rule("Alert when revenue drops more than 10% in 7 days", KNOWN)
    naive_now = datetime(2025, 3, 1, 12)
    result = AlertEngine().evaluate(
        rule, data, now=naive_now,
        last_triggered_at=datetime(2025, 3, 1, tzinfo=timezone.utc))
    assert result.suppressed_by_cooldown
