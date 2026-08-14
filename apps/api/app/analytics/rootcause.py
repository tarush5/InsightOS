"""
Root-cause / driver attribution engine (spec section 24).

Given an additive metric measured over two periods and one or more dimensions,
decompose the total change into per-segment contributions and rank them.

The decomposition is exact and deterministic -- the LLM never computes these
numbers, it only narrates them. For an additive metric M split by segment s:

    delta_total = sum_s (M_curr[s] - M_prev[s])

Each segment's contribution to the headline percentage change is therefore

    contribution_pct[s] = (M_curr[s] - M_prev[s]) / M_prev_total

and these sum exactly to the headline change. Segments absent from one period
are reported separately as entries/exits rather than folded into a rate change,
because "-100%" on a churned segment is misleading in a ranked list.

For metrics that are ratios of two additive quantities (e.g. revenue per order),
we also run a mix/rate decomposition, which separates "each segment got worse"
from "the mix shifted toward weaker segments" -- a distinction that changes the
recommended action entirely.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(slots=True)
class Driver:
    dimension: str
    segment: str
    prev_value: float
    curr_value: float
    absolute_change: float
    segment_pct_change: float | None      # change within the segment
    contribution_pct: float               # share of the TOTAL change, signed
    share_of_change: float                # |contribution| / |total change|
    status: str                           # "changed" | "new" | "lost"

    def as_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "segment": self.segment,
            "prev_value": round(self.prev_value, 4),
            "curr_value": round(self.curr_value, 4),
            "absolute_change": round(self.absolute_change, 4),
            "segment_pct_change": (
                None if self.segment_pct_change is None else round(self.segment_pct_change, 6)
            ),
            "contribution_pct": round(self.contribution_pct, 6),
            "share_of_change": round(self.share_of_change, 6),
            "status": self.status,
        }


@dataclass(slots=True)
class MixRateSplit:
    """How much of a ratio metric's change came from mix vs within-segment rate."""
    mix_effect: float
    rate_effect: float
    interaction: float

    @property
    def dominant(self) -> str:
        return "mix" if abs(self.mix_effect) > abs(self.rate_effect) else "rate"


@dataclass(slots=True)
class RootCauseResult:
    metric: str
    prev_total: float
    curr_total: float
    absolute_change: float
    pct_change: float
    drivers: list[Driver] = field(default_factory=list)
    mix_rate: dict[str, MixRateSplit] = field(default_factory=dict)
    coverage: float = 0.0        # fraction of total change explained by top drivers
    reconciliation_error: float = 0.0  # must be ~0; guards silent arithmetic bugs
    skipped_dimensions: list[str] = field(default_factory=list)

    def top(self, n: int = 5) -> list[Driver]:
        return self.drivers[:n]

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "prev_total": round(self.prev_total, 2),
            "curr_total": round(self.curr_total, 2),
            "absolute_change": round(self.absolute_change, 2),
            "pct_change": round(self.pct_change, 6),
            "skipped_dimensions": self.skipped_dimensions,
            "drivers": [d.as_dict() for d in self.drivers],
            "mix_rate": {
                k: {"mix_effect": round(v.mix_effect, 6),
                    "rate_effect": round(v.rate_effect, 6),
                    "interaction": round(v.interaction, 6),
                    "dominant": v.dominant}
                for k, v in self.mix_rate.items()
            },
            "coverage": round(self.coverage, 4),
            "reconciliation_error": round(self.reconciliation_error, 9),
        }


class RootCauseEngine:
    def __init__(self, *, min_contribution: float = 0.005) -> None:
        # Segments contributing under 0.5% of the total are noise in a ranked list.
        self.min_contribution = min_contribution

    def analyse(
        self,
        prev: pd.DataFrame,
        curr: pd.DataFrame,
        *,
        metric_column: str,
        dimensions: list[str],
        metric_name: str = "metric",
        denominator_column: str | None = None,
    ) -> RootCauseResult:
        prev_total = float(prev[metric_column].sum())
        curr_total = float(curr[metric_column].sum())
        change = curr_total - prev_total
        pct_change = change / prev_total if prev_total else float("nan")

        drivers: list[Driver] = []
        mix_rate: dict[str, MixRateSplit] = {}
        # Reconciliation must be computed across ALL segments, before the
        # min_contribution filter, or dropping small segments would look like an
        # arithmetic bug to the critic.
        unfiltered_sum: dict[str, float] = {}

        skipped: list[str] = []
        for dim in dimensions:
            if dim not in prev.columns or dim not in curr.columns:
                continue
            # A dimension with a single value cannot explain anything: its one
            # "segment" is just the total restated, and it would otherwise rank
            # first with 100% of the change. Report it as skipped rather than
            # silently dropping it, so a caller who passed a constant column
            # finds out why it never appears.
            cardinality = len(set(prev[dim].unique()) | set(curr[dim].unique()))
            if cardinality < 2:
                skipped.append(dim)
                continue
            p = prev.groupby(dim, dropna=False)[metric_column].sum()
            c = curr.groupby(dim, dropna=False)[metric_column].sum()
            unfiltered_sum[dim] = float(c.sum() - p.sum()) / prev_total if prev_total else 0.0
            for seg in sorted(set(p.index) | set(c.index), key=str):
                pv, cv = float(p.get(seg, 0.0)), float(c.get(seg, 0.0))
                delta = cv - pv
                if pv == 0 and cv == 0:
                    continue
                status = "changed" if pv and cv else ("new" if not pv else "lost")
                seg_pct = (delta / pv) if pv else None
                contribution = delta / prev_total if prev_total else 0.0
                if abs(contribution) < self.min_contribution:
                    continue
                drivers.append(Driver(
                    dimension=dim,
                    segment=str(seg),
                    prev_value=pv,
                    curr_value=cv,
                    absolute_change=delta,
                    segment_pct_change=seg_pct,
                    contribution_pct=contribution,
                    share_of_change=abs(delta) / abs(change) if change else 0.0,
                    status=status,
                ))

            if denominator_column:
                split = self._mix_rate(prev, curr, dim, metric_column, denominator_column)
                if split:
                    mix_rate[dim] = split

        drivers.sort(key=lambda d: abs(d.contribution_pct), reverse=True)

        # Reconciliation: within a single dimension, contributions must sum to the
        # headline change. We verify on the first dimension and surface the residual.
        recon_error = 0.0
        if unfiltered_sum:
            recon_error = max(abs(v - pct_change) for v in unfiltered_sum.values())

        coverage = 0.0
        if change and drivers:
            # Coverage is measured within the dimension that actually explains
            # the change, not whichever one the caller happened to list first.
            top_dim = drivers[0].dimension
            top_five = [d for d in drivers if d.dimension == top_dim][:5]
            coverage = min(1.0, sum(abs(d.absolute_change) for d in top_five) / abs(change))

        return RootCauseResult(
            metric=metric_name,
            prev_total=prev_total,
            curr_total=curr_total,
            absolute_change=change,
            pct_change=pct_change,
            drivers=drivers,
            mix_rate=mix_rate,
            coverage=coverage,
            skipped_dimensions=skipped,
            reconciliation_error=recon_error,
        )

    @staticmethod
    def _mix_rate(
        prev: pd.DataFrame,
        curr: pd.DataFrame,
        dim: str,
        numerator: str,
        denominator: str,
    ) -> MixRateSplit | None:
        """Classic mix/rate/interaction decomposition of a ratio metric."""
        if denominator not in prev.columns or denominator not in curr.columns:
            return None
        p = prev.groupby(dim, dropna=False)[[numerator, denominator]].sum()
        c = curr.groupby(dim, dropna=False)[[numerator, denominator]].sum()
        idx = sorted(set(p.index) | set(c.index), key=str)
        p, c = p.reindex(idx).fillna(0.0), c.reindex(idx).fillna(0.0)

        p_den_total, c_den_total = p[denominator].sum(), c[denominator].sum()
        if not p_den_total or not c_den_total:
            return None

        p_w, c_w = p[denominator] / p_den_total, c[denominator] / c_den_total
        with np.errstate(divide="ignore", invalid="ignore"):
            p_rate = np.where(p[denominator] > 0, p[numerator] / p[denominator], 0.0)
            c_rate = np.where(c[denominator] > 0, c[numerator] / c[denominator], 0.0)

        mix = float(np.sum((c_w - p_w) * p_rate))
        rate = float(np.sum(p_w * (c_rate - p_rate)))
        interaction = float(np.sum((c_w - p_w) * (c_rate - p_rate)))
        return MixRateSplit(mix_effect=mix, rate_effect=rate, interaction=interaction)
