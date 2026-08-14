"""
Scenario simulation (spec section 27).

What this is: arithmetic propagation of stated assumptions through a segment
decomposition and a forecast baseline. Given "recover South enterprise by 40%
over six weeks", it computes what the metric does, on a ramp, with the
uncertainty of the underlying forecast carried through.

What this is not: a causal model. Nothing here knows whether a lever *can* be
pulled, or what pulling it costs elsewhere. A simulator that hides that
distinction produces confident numbers for decisions it has no standing to
inform, so every result carries the assumption list that generated it, and
every lever the user did not justify is marked as asserted rather than
estimated.

The sensitivity pass exists for the same reason. A single scenario number
invites false precision; the tornado shows which assumption the answer actually
rests on, so the argument moves to the assumption that matters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np


class LeverBasis(StrEnum):
    ASSERTED = "asserted"        # the user stated it; we are propagating, not endorsing
    HISTORICAL = "historical"    # derived from an observed prior recovery
    MODELLED = "modelled"        # derived from a fitted model with reported error


@dataclass(slots=True)
class Lever:
    """A stated change to one segment of the metric."""
    segment: str
    change_pct: float                  # relative change to that segment, e.g. -0.4 or +0.25
    basis: LeverBasis = LeverBasis.ASSERTED
    ramp_days: int = 0                 # 0 = immediate; otherwise linear ramp to full effect
    rationale: str = ""

    def factor_at(self, day: int) -> float:
        if self.ramp_days <= 0:
            return 1.0 + self.change_pct
        progress = min(1.0, (day + 1) / self.ramp_days)
        return 1.0 + self.change_pct * progress


@dataclass(slots=True)
class LeverOutcome:
    segment: str
    baseline: float
    scenario: float
    delta: float
    share_of_total_delta: float
    basis: str
    rationale: str

    def as_dict(self) -> dict:
        return {"segment": self.segment,
                "baseline": round(self.baseline, 2),
                "scenario": round(self.scenario, 2),
                "delta": round(self.delta, 2),
                "share_of_total_delta": round(self.share_of_total_delta, 4),
                "basis": self.basis, "rationale": self.rationale}


@dataclass(slots=True)
class SensitivityBand:
    """How much the answer moves when one assumption is varied on its own."""
    segment: str
    low_delta: float
    high_delta: float
    swing: float

    def as_dict(self) -> dict:
        return {"segment": self.segment, "low_delta": round(self.low_delta, 2),
                "high_delta": round(self.high_delta, 2), "swing": round(self.swing, 2)}


@dataclass(slots=True)
class ScenarioResult:
    horizon_days: int
    baseline_total: float
    scenario_total: float
    delta: float
    delta_pct: float
    per_lever: list[LeverOutcome]
    daily_baseline: list[float]
    daily_scenario: list[float]
    sensitivity: list[SensitivityBand]
    assumptions: list[str]
    unaddressed_segments: list[str] = field(default_factory=list)
    interval_low: float | None = None
    interval_high: float | None = None

    def as_dict(self) -> dict:
        return {
            "horizon_days": self.horizon_days,
            "baseline_total": round(self.baseline_total, 2),
            "scenario_total": round(self.scenario_total, 2),
            "delta": round(self.delta, 2),
            "delta_pct": round(self.delta_pct, 6),
            "interval_95": (None if self.interval_low is None
                            else [round(self.interval_low, 2),
                                  round(self.interval_high or 0.0, 2)]),
            "per_lever": [o.as_dict() for o in self.per_lever],
            "sensitivity": [s.as_dict() for s in self.sensitivity],
            "daily_baseline": [round(v, 2) for v in self.daily_baseline],
            "daily_scenario": [round(v, 2) for v in self.daily_scenario],
            "assumptions": self.assumptions,
            "unaddressed_segments": self.unaddressed_segments,
        }

    def summary(self) -> str:
        direction = "adds" if self.delta >= 0 else "costs"
        return (f"Over {self.horizon_days} days the scenario {direction} "
                f"{abs(self.delta):,.0f} against a baseline of "
                f"{self.baseline_total:,.0f} ({self.delta_pct * 100:+.2f}%). "
                + (f"The result is most sensitive to {self.sensitivity[0].segment}, "
                   f"which alone swings it by {self.sensitivity[0].swing:,.0f}."
                   if self.sensitivity else ""))


class UnknownSegment(ValueError):
    """A lever names a segment that is not in the decomposition."""


class ScenarioSimulator:
    """Propagates levers over a per-segment baseline.

    ``segment_baselines`` maps segment name -> that segment's *daily* baseline
    contribution. Callers normally derive it from the root-cause decomposition
    and the forecast, so the parts sum to the projected whole.
    """

    def __init__(self, *, sensitivity_range: float = 0.5) -> None:
        self.sensitivity_range = sensitivity_range

    def simulate(self, segment_baselines: dict[str, float], levers: list[Lever],
                 *, horizon_days: int = 90,
                 baseline_interval_pct: float | None = None) -> ScenarioResult:
        if horizon_days <= 0:
            raise ValueError("horizon_days must be positive.")
        if not segment_baselines:
            raise ValueError("No segment baselines were supplied.")

        unknown = [lv.segment for lv in levers if lv.segment not in segment_baselines]
        if unknown:
            raise UnknownSegment(
                f"Lever(s) reference unknown segment(s): {sorted(set(unknown))}. "
                f"Known segments: {sorted(segment_baselines)}.")

        daily_base, daily_scen = self._paths(segment_baselines, levers, horizon_days)
        baseline_total = float(daily_base.sum())
        scenario_total = float(daily_scen.sum())
        delta = scenario_total - baseline_total

        per_lever = self._per_lever(segment_baselines, levers, horizon_days, delta)
        sensitivity = self._sensitivity(segment_baselines, levers, horizon_days)

        addressed = {lv.segment for lv in levers}
        unaddressed = sorted(set(segment_baselines) - addressed)

        interval = None
        if baseline_interval_pct is not None:
            spread = abs(scenario_total) * baseline_interval_pct
            interval = (scenario_total - spread, scenario_total + spread)

        return ScenarioResult(
            horizon_days=horizon_days,
            baseline_total=baseline_total, scenario_total=scenario_total,
            delta=delta,
            delta_pct=delta / baseline_total if abs(baseline_total) > 1e-12 else 0.0,
            per_lever=per_lever,
            daily_baseline=daily_base.tolist(), daily_scenario=daily_scen.tolist(),
            sensitivity=sensitivity,
            assumptions=self._assumptions(levers, unaddressed, baseline_interval_pct),
            unaddressed_segments=unaddressed,
            interval_low=None if interval is None else interval[0],
            interval_high=None if interval is None else interval[1],
        )

    def break_even(self, segment_baselines: dict[str, float], segment: str,
                   *, target_delta: float, horizon_days: int = 90,
                   ramp_days: int = 0) -> float | None:
        """The change_pct on one segment that would produce ``target_delta``.
        Returns None when the segment cannot reach the target at any magnitude."""
        if segment not in segment_baselines:
            raise UnknownSegment(f"Unknown segment '{segment}'.")
        probe = Lever(segment=segment, change_pct=1.0, ramp_days=ramp_days)
        unit = self.simulate(segment_baselines, [probe],
                             horizon_days=horizon_days).delta
        if abs(unit) < 1e-12:
            return None
        return target_delta / unit          # linear in change_pct by construction

    # --- internals -------------------------------------------------------
    @staticmethod
    def _paths(baselines: dict[str, float], levers: list[Lever],
               horizon: int) -> tuple[np.ndarray, np.ndarray]:
        days = np.arange(horizon)
        base = np.zeros(horizon)
        scen = np.zeros(horizon)
        by_segment: dict[str, list[Lever]] = {}
        for lv in levers:
            by_segment.setdefault(lv.segment, []).append(lv)

        for segment, daily in baselines.items():
            contribution = np.full(horizon, float(daily))
            base += contribution
            factor = np.ones(horizon)
            for lv in by_segment.get(segment, []):
                # Multiple levers on one segment compound rather than add, so
                # two 50% cuts leave 25%, not zero.
                factor = factor * np.array([lv.factor_at(int(d)) for d in days])
            scen += contribution * factor
        return base, scen

    def _per_lever(self, baselines: dict[str, float], levers: list[Lever],
                   horizon: int, total_delta: float) -> list[LeverOutcome]:
        out: list[LeverOutcome] = []
        for lv in levers:
            daily = baselines[lv.segment]
            base = daily * horizon
            single = self._paths({lv.segment: daily}, [lv], horizon)[1].sum()
            delta = float(single) - base
            out.append(LeverOutcome(
                segment=lv.segment, baseline=base, scenario=float(single), delta=delta,
                share_of_total_delta=(delta / total_delta
                                      if abs(total_delta) > 1e-12 else 0.0),
                basis=str(lv.basis), rationale=lv.rationale))
        return sorted(out, key=lambda o: abs(o.delta), reverse=True)

    def _sensitivity(self, baselines: dict[str, float], levers: list[Lever],
                     horizon: int) -> list[SensitivityBand]:
        """Vary one lever at a time by +/- ``sensitivity_range`` of its own
        magnitude, holding the rest fixed."""
        bands: list[SensitivityBand] = []
        centre = self._paths(baselines, levers, horizon)[1].sum()
        for i, lv in enumerate(levers):
            deltas = []
            for scale in (1 - self.sensitivity_range, 1 + self.sensitivity_range):
                variant = list(levers)
                variant[i] = Lever(segment=lv.segment,
                                   change_pct=lv.change_pct * scale,
                                   basis=lv.basis, ramp_days=lv.ramp_days,
                                   rationale=lv.rationale)
                deltas.append(float(self._paths(baselines, variant, horizon)[1].sum())
                              - float(centre))
            low, high = sorted(deltas)
            bands.append(SensitivityBand(lv.segment, low, high, high - low))
        return sorted(bands, key=lambda b: b.swing, reverse=True)

    @staticmethod
    def _assumptions(levers: list[Lever], unaddressed: list[str],
                     interval_pct: float | None) -> list[str]:
        out = [
            "Segments are treated as independent: a change to one does not move "
            "the others. Cannibalisation and cross-sell are not modelled.",
            "The baseline continues unchanged in the absence of the levers.",
        ]
        asserted = [lv.segment for lv in levers if lv.basis is LeverBasis.ASSERTED]
        if asserted:
            out.append(
                "Lever magnitudes for " + ", ".join(sorted(set(asserted)))
                + " were asserted, not estimated from data. The output inherits "
                  "whatever confidence you have in them and adds none of its own.")
        if any(lv.ramp_days > 0 for lv in levers):
            out.append("Ramps are linear; real interventions rarely are.")
        if unaddressed:
            out.append(f"{len(unaddressed)} segment(s) carry no lever and are held "
                       "flat at baseline.")
        if interval_pct is None:
            out.append("No forecast interval was supplied, so the projection is a "
                       "point estimate with no stated uncertainty.")
        return out
