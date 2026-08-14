"""
Building a panel from daily rows.

Resampling daily data to weeks or months creates a trap at both ends of the
range: the first and last buckets usually hold a partial period. A week
containing two days looks like a 70% collapse, and a causal or trend estimator
reads that as signal. The effect is largest exactly where it does the most
damage -- at the boundaries, which anchor a fitted trend.

So incomplete boundary periods are dropped and *reported*. Silently trimming
data is its own failure mode; the caller gets told which periods were removed
and why, and can widen the range if the trim cost them something they wanted.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

GRAIN_FREQ = {"day": "D", "week": "W", "month": "M"}
GRAIN_DAYS = {"day": 1, "week": 7, "month": 28}

# A period must be at least this fraction populated to be kept. Below it the
# bucket is measuring calendar boundaries rather than the business.
MIN_PERIOD_COVERAGE = 0.8


@dataclass(slots=True)
class PanelBuild:
    frame: pd.DataFrame
    grain: str
    dropped_periods: list[str] = field(default_factory=list)
    kept_periods: int = 0

    def as_note(self) -> str | None:
        if not self.dropped_periods:
            return None
        return (f"Dropped {len(self.dropped_periods)} incomplete "
                f"{self.grain}(s) at the range boundary "
                f"({', '.join(self.dropped_periods)}); a partial period would "
                f"read as a sharp move that is really just a calendar edge.")

    def as_dict(self) -> dict:
        return {"grain": self.grain, "periods": self.kept_periods,
                "dropped_periods": self.dropped_periods, "note": self.as_note()}


def build_panel(frame: pd.DataFrame, *, date_col: str, unit_col: str,
                value_col: str, grain: str = "week",
                min_coverage: float = MIN_PERIOD_COVERAGE) -> PanelBuild:
    """Aggregate daily rows into a long panel of (unit, period, outcome)."""
    if grain not in GRAIN_FREQ:
        raise ValueError(f"grain must be one of {sorted(GRAIN_FREQ)}")
    for col in (date_col, unit_col, value_col):
        if col not in frame.columns:
            raise ValueError(f"Panel source is missing column '{col}'.")

    work = frame[[date_col, unit_col, value_col]].dropna().copy()
    work[date_col] = pd.to_datetime(work[date_col])
    work["period"] = work[date_col].dt.to_period(GRAIN_FREQ[grain]).dt.start_time

    dropped: list[str] = []
    if grain != "day":
        # Coverage is counted on distinct calendar days present anywhere in the
        # panel for that period -- not per unit, since a unit legitimately
        # missing a day should not disqualify the period for everyone.
        days = work.groupby("period")[date_col].nunique()
        expected = GRAIN_DAYS[grain]
        if grain == "month":
            # Real month lengths, not a 28-day approximation: February at 28/28
            # is complete, while 28 days of a 31-day month is not.
            import numpy as np

            expected_by_period = np.array([ts.days_in_month for ts in days.index],
                                          dtype=float)
            coverage = days.to_numpy() / expected_by_period
        else:
            coverage = days.to_numpy() / expected

        incomplete = days.index[coverage < min_coverage]
        # Only boundary periods are dropped. A gap in the middle is a data
        # problem the caller needs to see, not something to quietly delete.
        boundaries = {days.index.min(), days.index.max()}
        to_drop = [p for p in incomplete if p in boundaries]
        if to_drop:
            dropped = [str(p.date()) for p in sorted(to_drop)]
            work = work[~work["period"].isin(to_drop)]

    panel = (work.groupby([unit_col, "period"], as_index=False)[value_col].sum()
             .rename(columns={unit_col: "unit", value_col: "outcome"}))
    return PanelBuild(frame=panel, grain=grain, dropped_periods=dropped,
                      kept_periods=int(panel["period"].nunique()))
