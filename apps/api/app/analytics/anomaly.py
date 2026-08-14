"""
Anomaly detection (spec section 23).

Three detectors, chosen by data shape rather than by preference:

* ``robust_zscore``  -- median/MAD. Survives the very outliers it is looking for,
  unlike mean/std which get dragged toward the anomaly.
* ``seasonal_residual`` -- subtracts a period-over-period baseline (default 7 days)
  before scoring, so Monday troughs are not reported as incidents.
* ``rolling_deviation`` -- trailing window, for series with drift.

Every anomaly carries observed vs expected, deviation, and severity, per spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

_MAD_TO_SIGMA = 1.4826  # makes MAD a consistent estimator of sigma for normal data


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


def _severity(score: float) -> Severity:
    a = abs(score)
    if a >= 6.0:
        return Severity.CRITICAL
    if a >= 4.0:
        return Severity.HIGH
    if a >= 3.0:
        return Severity.MEDIUM
    return Severity.LOW


@dataclass(slots=True)
class Anomaly:
    timestamp: str
    metric: str
    observed: float
    expected: float
    deviation: float          # observed - expected
    deviation_pct: float
    score: float              # robust z-score
    severity: Severity
    direction: str            # "spike" | "drop"
    method: str
    segment: str | None = None

    def as_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "metric": self.metric,
            "observed": round(self.observed, 4),
            "expected": round(self.expected, 4),
            "deviation": round(self.deviation, 4),
            "deviation_pct": round(self.deviation_pct, 6),
            "score": round(self.score, 3),
            "severity": str(self.severity),
            "direction": self.direction,
            "method": self.method,
            "segment": self.segment,
        }


class AnomalyDetector:
    def __init__(self, *, threshold: float = 3.0, seasonal_period: int = 7) -> None:
        self.threshold = threshold
        self.seasonal_period = seasonal_period

    def detect(
        self,
        series: pd.Series,
        *,
        metric: str = "metric",
        method: str = "auto",
        segment: str | None = None,
    ) -> list[Anomaly]:
        s = series.dropna().astype(float)
        if len(s) < 8:
            return []

        if method == "auto":
            method = self._choose(s)

        if method == "seasonal_residual":
            expected = s.shift(self.seasonal_period).rolling(
                self.seasonal_period, min_periods=1
            ).median()
            expected = expected.bfill()
        elif method == "rolling_deviation":
            window = max(7, min(28, len(s) // 4))
            expected = s.rolling(window, min_periods=3).median().bfill()
        else:
            method = "robust_zscore"
            expected = pd.Series(float(np.median(s)), index=s.index)

        residual = s - expected
        mad = float(np.median(np.abs(residual - np.median(residual))))
        scale = mad * _MAD_TO_SIGMA
        if scale <= 1e-12:
            scale = float(residual.std(ddof=1)) or 1e-9

        scores = residual / scale
        out: list[Anomaly] = []
        for ts, score in scores.items():
            if abs(score) < self.threshold or not np.isfinite(score):
                continue
            obs, exp = float(s.loc[ts]), float(expected.loc[ts])
            out.append(Anomaly(
                timestamp=str(ts.date() if hasattr(ts, "date") else ts),
                metric=metric,
                observed=obs,
                expected=exp,
                deviation=obs - exp,
                deviation_pct=(obs - exp) / exp if exp else float("nan"),
                score=float(score),
                severity=_severity(float(score)),
                direction="spike" if score > 0 else "drop",
                method=method,
                segment=segment,
            ))
        return sorted(out, key=lambda a: abs(a.score), reverse=True)

    def _choose(self, s: pd.Series) -> str:
        """Pick a detector from the data, not from a config file."""
        if len(s) >= self.seasonal_period * 3:
            # If lag-`period` autocorrelation is high, the series is seasonal.
            lagged = s.shift(self.seasonal_period).dropna()
            aligned = s.loc[lagged.index]
            if len(lagged) > 5:
                corr = float(np.corrcoef(aligned, lagged)[0, 1])
                if np.isfinite(corr) and corr > 0.55:
                    return "seasonal_residual"
        # Detect drift via first/last-third mean shift relative to spread.
        third = max(3, len(s) // 3)
        shift = abs(s.iloc[-third:].mean() - s.iloc[:third].mean())
        if s.std(ddof=1) and shift > s.std(ddof=1):
            return "rolling_deviation"
        return "robust_zscore"
