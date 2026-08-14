"""
Causal estimation and scenario simulation routes (spec sections 26-27).

Both endpoints return their diagnostics and assumptions in the response body
rather than in documentation. The failure mode this guards against is a client
rendering a large number in a headline and dropping everything that qualifies
it -- if the caveats travel with the estimate, dropping them is a visible
choice rather than an accident.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.analytics.causal import DiffInDiff, InsufficientDesign
from app.analytics.panels import GRAIN_FREQ, build_panel
from app.analytics.simulation import (Lever, LeverBasis, ScenarioSimulator,
                                      UnknownSegment)
from app.api.deps import Principal, requires
from app.core.security import Permission
from app.data.demo import demo_data_provider
from app.semantic.registry import default_registry

router = APIRouter()




class CausalRequest(BaseModel):
    metric_key: str
    dimension: str = Field(default="region", max_length=64)
    treated_units: list[str] = Field(min_length=1, max_length=20)
    control_units: list[str] | None = None
    treatment_date: date
    start: date
    end: date
    grain: str = Field(default="week")

    @field_validator("grain")
    @classmethod
    def _known_grain(cls, v: str) -> str:
        if v not in GRAIN_FREQ:
            raise ValueError(f"grain must be one of {sorted(GRAIN_FREQ)}")
        return v


class LeverSpec(BaseModel):
    segment: str
    change_pct: float = Field(ge=-1.0, le=10.0)
    ramp_days: int = Field(default=0, ge=0, le=365)
    basis: str = Field(default="asserted")
    rationale: str = Field(default="", max_length=400)

    @field_validator("basis")
    @classmethod
    def _known_basis(cls, v: str) -> str:
        if v not in {b.value for b in LeverBasis}:
            raise ValueError(f"basis must be one of {[b.value for b in LeverBasis]}")
        return v


class SimulationRequest(BaseModel):
    metric_key: str
    dimension: str = Field(default="region", max_length=64)
    baseline_start: date
    baseline_end: date
    levers: list[LeverSpec] = Field(min_length=1, max_length=20)
    horizon_days: int = Field(default=90, ge=7, le=365)


class BreakEvenRequest(BaseModel):
    metric_key: str
    dimension: str = Field(default="region", max_length=64)
    baseline_start: date
    baseline_end: date
    segment: str
    target_delta: float
    horizon_days: int = Field(default=90, ge=7, le=365)


def _panel(metric_key: str, dimension: str, start: date, end: date,
           grain: str = "week"):
    metric = _metric(metric_key)
    frame = demo_data_provider(metric, start, end)
    if dimension not in frame.columns:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Metric '{metric_key}' is not broken out by '{dimension}'. "
            f"Available: {', '.join(c for c in frame.columns if c != metric_key)}")
    return build_panel(frame, date_col=metric.date_column, unit_col=dimension,
                       value_col=metric_key, grain=grain)


def _metric(metric_key: str):
    metric = default_registry().get(metric_key)
    if metric is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"Unknown metric '{metric_key}'.")
    return metric


def _segment_daily_baselines(metric_key: str, dimension: str,
                             start: date, end: date) -> dict[str, float]:
    metric = _metric(metric_key)
    frame = demo_data_provider(metric, start, end)
    if dimension not in frame.columns:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Metric '{metric_key}' has no '{dimension}' breakdown.")
    span = max((end - start).days + 1, 1)
    totals = frame.groupby(dimension)[metric_key].sum()
    return {str(k): float(v) / span for k, v in totals.items()}


@router.post("/causal/diff-in-diff")
async def diff_in_diff(
    payload: CausalRequest,
    principal: Principal = Depends(requires(Permission.INVESTIGATION_RUN)),
) -> dict:
    try:
        build = _panel(payload.metric_key, payload.dimension,
                       payload.start, payload.end, payload.grain)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    try:
        estimate = DiffInDiff().estimate(
            build.frame, treated_units=payload.treated_units,
            control_units=payload.control_units,
            treatment_period=payload.treatment_date)
    except InsufficientDesign as exc:
        # Not a 500: the request was well-formed, the data simply cannot
        # support the design. The message says which part is missing.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "insufficient_design", "message": str(exc)})

    return {"metric_key": payload.metric_key, "dimension": payload.dimension,
            "treated_units": payload.treated_units,
            "treatment_date": str(payload.treatment_date),
            "panel": build.as_dict(), "estimate": estimate.as_dict()}


@router.post("/simulate")
async def simulate(
    payload: SimulationRequest,
    principal: Principal = Depends(requires(Permission.SIMULATION_RUN)),
) -> dict:
    try:
        baselines = _segment_daily_baselines(
            payload.metric_key, payload.dimension,
            payload.baseline_start, payload.baseline_end)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    levers = [Lever(segment=spec.segment, change_pct=spec.change_pct,
                    basis=LeverBasis(spec.basis), ramp_days=spec.ramp_days,
                    rationale=spec.rationale)
              for spec in payload.levers]
    try:
        result = ScenarioSimulator().simulate(baselines, levers,
                                              horizon_days=payload.horizon_days)
    except UnknownSegment as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    return {"metric_key": payload.metric_key, "dimension": payload.dimension,
            "baseline_window": [str(payload.baseline_start), str(payload.baseline_end)],
            "summary": result.summary(), "result": result.as_dict()}


@router.post("/simulate/break-even")
async def break_even(
    payload: BreakEvenRequest,
    principal: Principal = Depends(requires(Permission.SIMULATION_RUN)),
) -> dict:
    try:
        baselines = _segment_daily_baselines(
            payload.metric_key, payload.dimension,
            payload.baseline_start, payload.baseline_end)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    simulator = ScenarioSimulator()
    try:
        required = simulator.break_even(baselines, payload.segment,
                                        target_delta=payload.target_delta,
                                        horizon_days=payload.horizon_days)
    except UnknownSegment as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))

    if required is None:
        return {"reachable": False,
                "message": f"'{payload.segment}' contributes nothing over this "
                           "window, so no change to it can reach the target."}
    return {
        "reachable": abs(required) <= 10.0,
        "segment": payload.segment,
        "required_change_pct": round(required, 6),
        "target_delta": payload.target_delta,
        "horizon_days": payload.horizon_days,
        "note": ("A change of this size has no precedent in the baseline window; "
                 "treat it as a bound, not a plan."
                 if abs(required) > 1.0 else
                 "Linear in the lever by construction: doubling the change "
                 "doubles the effect, which is only true while the segments "
                 "stay independent."),
    }
