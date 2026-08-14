"""
Alert evaluation.

Every evaluation returns the observed value, the threshold it was compared
against, and the window it covered -- whether or not it fired. A silent
"no alert" is indistinguishable from a broken monitor, so the engine reports
its reasoning either way and the caller decides what to persist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.alerts.rules import AlertRule, Condition
from app.analytics.anomaly import AnomalyDetector, Severity

_SEVERITY_ORDER = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2,
                   Severity.CRITICAL: 3}

# Fraction of the comparison window that must actually contain observations
# before a percentage change is considered meaningful.
MIN_WINDOW_COVERAGE = 0.6


@dataclass(slots=True)
class AlertEvaluation:
    rule: AlertRule
    triggered: bool
    reason: str
    observed: float | None = None
    threshold: float | None = None
    window_start: str | None = None
    window_end: str | None = None
    severity: str = "medium"
    evidence: dict = field(default_factory=dict)
    suppressed_by_cooldown: bool = False

    def as_dict(self) -> dict:
        return {
            "triggered": self.triggered,
            "suppressed_by_cooldown": self.suppressed_by_cooldown,
            "reason": self.reason,
            "observed": None if self.observed is None else round(float(self.observed), 4),
            "threshold": None if self.threshold is None else round(float(self.threshold), 4),
            "window": {"start": self.window_start, "end": self.window_end},
            "severity": self.severity,
            "rule": self.rule.as_dict(),
            "evidence": self.evidence,
        }


class AlertEngine:
    """Stateless evaluator. Cooldown state is passed in, not held."""

    def __init__(self, detector: AnomalyDetector | None = None) -> None:
        self.detector = detector or AnomalyDetector()

    def evaluate(self, rule: AlertRule, series: pd.Series, *,
                 now: datetime | None = None,
                 last_triggered_at: datetime | None = None) -> AlertEvaluation:
        now = now or datetime.now(timezone.utc)
        s = series.dropna().astype(float).sort_index()
        if s.empty:
            return AlertEvaluation(rule, False, "No data in the evaluation window.")

        window_end = s.index.max()
        window_start = window_end - pd.Timedelta(days=rule.window_days - 1)
        current = s.loc[s.index >= window_start]
        if current.empty:
            return AlertEvaluation(rule, False, "No observations inside the window.")

        result = {
            Condition.THRESHOLD: self._threshold,
            Condition.CHANGE: self._change,
            Condition.ANOMALY: self._anomaly,
        }[rule.condition](rule, s, current, window_start, window_end)

        if result.triggered and self._in_cooldown(rule, now, last_triggered_at):
            result.triggered = False
            result.suppressed_by_cooldown = True
            result.reason = (f"Condition met but suppressed: last fired within "
                             f"{rule.cooldown_hours}h cooldown.")
        return result

    # --- conditions ------------------------------------------------------
    def _threshold(self, rule, s, current, start, end) -> AlertEvaluation:
        observed = float(current.mean())
        breached = observed < rule.threshold if rule.operator == "lt" else observed > rule.threshold
        comparator = "below" if rule.operator == "lt" else "above"
        reason = (f"{rule.metric_key} averaged {observed:,.2f} over {rule.window_days}d, "
                  f"{'which is ' if breached else 'not '}{comparator} the "
                  f"threshold of {rule.threshold:,.2f}.")
        return AlertEvaluation(
            rule, breached, reason, observed, rule.threshold,
            str(start.date()), str(end.date()),
            severity=self._threshold_severity(observed, rule.threshold),
            evidence={"observations": int(current.size),
                      "min": float(current.min()), "max": float(current.max())})

    def _change(self, rule, s, current, start, end) -> AlertEvaluation:
        prior_end = start - pd.Timedelta(days=1)
        prior_start = prior_end - pd.Timedelta(days=rule.comparison_days - 1)
        prior = s.loc[(s.index >= prior_start) & (s.index <= prior_end)]

        # A partially-populated comparison window is worse than none: comparing
        # a full week against the two days that happen to exist manufactures a
        # change out of which days were present. Require most of the window.
        expected = max(rule.comparison_days, 1)
        coverage = len(prior) / expected
        if prior.empty or coverage < MIN_WINDOW_COVERAGE:
            return AlertEvaluation(
                rule, False,
                f"Comparison window {prior_start.date()}..{prior_end.date()} is "
                f"only {coverage:.0%} populated ({len(prior)} of {expected} days); "
                "a percentage change computed from it would reflect missing days "
                "rather than a real move. Alert not evaluated.",
                window_start=str(start.date()), window_end=str(end.date()),
                evidence={"comparison_coverage": round(coverage, 4),
                          "observations": len(prior)})

        curr_mean, prior_mean = float(current.mean()), float(prior.mean())
        if abs(prior_mean) < 1e-12:
            return AlertEvaluation(rule, False,
                                   "Comparison window averages zero; percentage "
                                   "change is undefined.",
                                   window_start=str(start.date()),
                                   window_end=str(end.date()))
        change = (curr_mean - prior_mean) / abs(prior_mean)
        breached = (change <= -rule.threshold if rule.operator == "lt"
                    else change >= rule.threshold)
        reason = (f"{rule.metric_key} moved {change * 100:+.2f}% "
                  f"({prior_mean:,.2f} -> {curr_mean:,.2f}) versus the prior "
                  f"{rule.comparison_days}d; threshold is "
                  f"{'-' if rule.operator == 'lt' else '+'}{rule.threshold * 100:g}%.")
        return AlertEvaluation(
            rule, breached, reason, change, rule.threshold,
            str(start.date()), str(end.date()),
            severity=self._change_severity(abs(change), rule.threshold),
            evidence={"current_mean": curr_mean, "prior_mean": prior_mean,
                      "prior_window": [str(prior_start.date()), str(prior_end.date())],
                      "change_pct": round(change * 100, 4)})

    def _anomaly(self, rule, s, current, start, end) -> AlertEvaluation:
        floor = _SEVERITY_ORDER[Severity(rule.min_severity)]
        found = [a for a in self.detector.detect(s, metric=rule.metric_key)
                 if _SEVERITY_ORDER[a.severity] >= floor
                 and pd.Timestamp(a.timestamp) >= start]
        if not found:
            return AlertEvaluation(
                rule, False,
                f"No {rule.min_severity}-or-worse anomalies in the last "
                f"{rule.window_days}d ({int(s.size)} points scanned).",
                window_start=str(start.date()), window_end=str(end.date()),
                evidence={"points_scanned": int(s.size)})
        worst = found[0]
        reason = (f"{len(found)} anomaly(ies) detected; worst on {worst.timestamp}: "
                  f"observed {worst.observed:,.2f} against an expected "
                  f"{worst.expected:,.2f} ({worst.deviation_pct * 100:+.1f}%, "
                  f"z={worst.score:.2f}, {worst.method}).")
        return AlertEvaluation(
            rule, True, reason, worst.observed, worst.expected,
            str(start.date()), str(end.date()), severity=str(worst.severity),
            evidence={"anomalies": [a.as_dict() for a in found[:10]],
                      "count": len(found)})

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def _in_cooldown(rule: AlertRule, now: datetime,
                     last: datetime | None) -> bool:
        if last is None or rule.cooldown_hours <= 0:
            return False
        # Callers pass a mix of naive timestamps (from a pandas index) and
        # aware ones (from the database). Normalising both here is safer than
        # trusting every call site to remember.
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - last) < timedelta(hours=rule.cooldown_hours)

    @staticmethod
    def _threshold_severity(observed: float, threshold: float) -> str:
        if abs(threshold) < 1e-12:
            return "medium"
        overshoot = abs(observed - threshold) / abs(threshold)
        return "critical" if overshoot >= 0.5 else "high" if overshoot >= 0.2 else "medium"

    @staticmethod
    def _change_severity(magnitude: float, threshold: float) -> str:
        if threshold <= 0:
            return "medium"
        ratio = magnitude / threshold
        return "critical" if ratio >= 3 else "high" if ratio >= 1.5 else "medium"
