"""
Alert routes (spec section 25).

Creating an alert is a two-step conversation by design. ``/preview`` compiles
the text, shows the structured rule and evaluates it against history so the
author sees how often it *would* have fired before committing. An alert that
turns out to fire daily is discovered here rather than at 3am.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.engine import AlertEngine
from app.alerts.rules import AlertRule, AmbiguousRule, compile_rule
from app.api.deps import Principal, current_principal, requires, tenant_session
from app.core.security import Permission
from app.data.demo import evaluation_anchor, metric_series
from app.repositories.alerts import AlertRepository
from app.repositories.audit import AuditRepository
from app.semantic.registry import default_registry

router = APIRouter()


class CompileRequest(BaseModel):
    text: str = Field(min_length=5, max_length=500)
    segment: dict[str, str] = Field(default_factory=dict)
    backtest_days: int = Field(default=180, ge=30, le=730)


class CreateAlert(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    text: str = Field(min_length=5, max_length=500)
    segment: dict[str, str] = Field(default_factory=dict)


def _compile(text: str, segment: dict[str, str]) -> AlertRule:
    registry = default_registry()
    known = {m.key: m.label for m in registry.all()}
    try:
        return compile_rule(text, known, segment=segment)
    except AmbiguousRule as exc:
        # 422 with the missing pieces named: the caller can ask one follow-up
        # question rather than guessing at the grammar.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "ambiguous_rule", "message": str(exc),
                    "missing": exc.missing,
                    "known_metrics": sorted(known)})


def _backtest(rule: AlertRule, days: int) -> dict:
    """Replay the rule over history, one evaluation per day, with cooldown
    applied -- so the count reflects pages a human would actually have received."""
    metric = default_registry().require_approved(rule.metric_key)
    end, stale = evaluation_anchor(metric)
    start = end - timedelta(days=days)
    try:
        series = metric_series(metric, start, end, rule.segment)
    except (FileNotFoundError, ValueError) as exc:
        return {"available": False, "reason": str(exc)}
    if series.empty:
        return {"available": False,
                "reason": "No history for this metric and segment."}

    engine = AlertEngine()
    fires: list[dict] = []
    last_fired = None
    dates = series.index.to_list()
    for i, day in enumerate(dates):
        window = series.iloc[: i + 1]
        if len(window) < max(rule.window_days + rule.comparison_days, 8):
            continue
        result = engine.evaluate(rule, window, now=day.to_pydatetime(),
                                 last_triggered_at=last_fired)
        if result.triggered:
            fires.append({"date": str(day.date()), "reason": result.reason,
                          "severity": result.severity})
            last_fired = day.to_pydatetime()

    span_days = max((dates[-1] - dates[0]).days, 1)
    per_month = len(fires) * 30.0 / span_days
    return {
        "available": True,
        "anchored_on": str(end),
        "warehouse_is_stale": stale,
        "history_days": span_days,
        "would_have_fired": len(fires),
        "estimated_per_month": round(per_month, 2),
        "noisy": per_month > 8,
        "first_fires": fires[:10],
    }


@router.post("/preview")
async def preview_alert(payload: CompileRequest,
                        principal: Principal = Depends(current_principal)) -> dict:
    rule = _compile(payload.text, payload.segment)
    return {"rule": rule.as_dict(), "readback": rule.describe(),
            "backtest": _backtest(rule, payload.backtest_days)}


@router.post("", status_code=201)
async def create_alert(payload: CreateAlert,
                       principal: Principal = Depends(requires(Permission.ALERT_MANAGE)),
                       session: AsyncSession = Depends(tenant_session)) -> dict:
    rule = _compile(payload.text, payload.segment)
    alert = await AlertRepository(session).create(
        workspace_id=principal.workspace_id, name=payload.name, rule=rule)
    await AuditRepository(session).record(
        action="alert.create", workspace_id=principal.workspace_id,
        user_id=principal.user_id, resource_type="alert", resource_id=str(alert.id))
    return {"id": str(alert.id), "name": alert.name, "rule": alert.rule,
            "readback": rule.describe(), "is_active": alert.is_active}


@router.get("")
async def list_alerts(principal: Principal = Depends(current_principal),
                      session: AsyncSession = Depends(tenant_session)) -> dict:
    alerts = await AlertRepository(session).list_all()
    return {"alerts": [
        {"id": str(a.id), "name": a.name, "metric_key": a.metric_key,
         "is_active": a.is_active, "rule": a.rule,
         "natural_language": a.natural_language,
         "last_triggered_at": (a.last_triggered_at.isoformat()
                               if a.last_triggered_at else None)}
        for a in alerts]}


@router.post("/{alert_id}/evaluate")
async def evaluate_alert(alert_id: uuid.UUID,
                         principal: Principal = Depends(current_principal),
                         session: AsyncSession = Depends(tenant_session)) -> dict:
    repo = AlertRepository(session)
    alert = await repo.get(alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")

    rule = repo.rule_of(alert)
    metric = default_registry().require_approved(rule.metric_key)
    end, stale = evaluation_anchor(metric)
    start = end - timedelta(days=max(rule.window_days + rule.comparison_days, 60))
    try:
        series = metric_series(metric, start, end, rule.segment)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    result = AlertEngine().evaluate(rule, series,
                                    last_triggered_at=alert.last_triggered_at)
    if result.triggered:
        await repo.mark_triggered(alert)
        await AuditRepository(session).record(
            action="alert.triggered", workspace_id=principal.workspace_id,
            resource_type="alert", resource_id=str(alert.id))
    return {**result.as_dict(), "anchored_on": str(end),
            "warehouse_is_stale": stale}


@router.patch("/{alert_id}")
async def set_alert_active(alert_id: uuid.UUID, is_active: bool,
                           principal: Principal = Depends(requires(Permission.ALERT_MANAGE)),
                           session: AsyncSession = Depends(tenant_session)) -> dict:
    repo = AlertRepository(session)
    alert = await repo.get(alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    await repo.set_active(alert, is_active)
    await AuditRepository(session).record(
        action="alert.activate" if is_active else "alert.deactivate",
        workspace_id=principal.workspace_id, user_id=principal.user_id,
        resource_type="alert", resource_id=str(alert.id))
    return {"id": str(alert.id), "is_active": alert.is_active}
