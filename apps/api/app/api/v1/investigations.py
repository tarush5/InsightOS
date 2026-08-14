"""
Investigation routes, including the WebSocket that streams agent progress.

Every run is persisted as it happens: the report, the stage timeline, and the
evidence each conclusion rests on. That is not bookkeeping -- an investigation
whose reasoning cannot be re-read later is not auditable, and an unauditable
answer is not one anybody should act on.

The socket is authenticated by a short-lived ticket rather than the access
token. WebSocket clients cannot set headers, so the credential has to travel in
the URL, where it lands in proxy logs and browser history; a 60-second ticket
limits what that leak is worth.
"""
from __future__ import annotations

import logging
import time
from datetime import date

import jwt
from fastapi import (APIRouter, Depends, HTTPException, Query, WebSocket,
                     WebSocketDisconnect, status)
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.orchestrator import InvestigationOrchestrator, InvestigationRequest
from app.api.deps import Principal, current_principal, requires, tenant_session
from app.core.security import Permission, Role, decode_token, issue_token
from app.data.demo import SeedDataMissing, demo_data_provider
from app.db.session import session_scope
from app.llm.gateway import LLMGateway
from app.repositories.audit import AuditRepository
from app.repositories.investigations import InvestigationRepository
from app.semantic.registry import default_registry

log = logging.getLogger(__name__)
router = APIRouter()


class StartInvestigation(BaseModel):
    question: str = Field(min_length=5, max_length=500)
    metric_key: str | None = None
    current_start: date | None = None
    current_end: date | None = None
    comparison_start: date | None = None
    comparison_end: date | None = None
    dimensions: list[str] = Field(
        default_factory=lambda: ["region", "segment", "channel"])
    forecast_horizon: int = Field(default=30, ge=7, le=90)


def _build_request(payload: StartInvestigation,
                   principal: Principal) -> InvestigationRequest:
    curr = comp = None
    if payload.current_start and payload.current_end:
        curr = (payload.current_start, payload.current_end)
    if payload.comparison_start and payload.comparison_end:
        comp = (payload.comparison_start, payload.comparison_end)
    return InvestigationRequest(
        question=payload.question, metric_key=payload.metric_key,
        current_period=curr, comparison_period=comp,
        dimensions=payload.dimensions, forecast_horizon=payload.forecast_horizon,
        workspace_id=str(principal.workspace_id), user_id=str(principal.user_id))


def _orchestrator() -> InvestigationOrchestrator:
    return InvestigationOrchestrator(registry=default_registry(),
                                     data_provider=demo_data_provider,
                                     gateway=LLMGateway())


async def _run_and_persist(payload: StartInvestigation, principal: Principal,
                           session: AsyncSession, *,
                           on_event=None) -> tuple[list[dict], str]:
    """One execution path for both HTTP and WebSocket, so a run streamed to a
    browser and a run posted from a script are recorded identically."""
    repo = InvestigationRepository(session)
    record = await repo.create(workspace_id=principal.workspace_id,
                               user_id=principal.user_id,
                               question=payload.question,
                               metric_key=payload.metric_key)
    events: list[dict] = []
    started = time.perf_counter()
    try:
        async for event in _orchestrator().run(_build_request(payload, principal)):
            data = event.as_dict()
            events.append(data)
            await repo.record_stage(record.id, data)
            if on_event is not None:
                await on_event(data)
    except Exception:
        log.exception("investigation.failed reference=%s", record.reference)
        await repo.finalise(record, {"error": "Investigation failed."},
                            duration_ms=int((time.perf_counter() - started) * 1000),
                            failed=True)
        raise

    elapsed = int((time.perf_counter() - started) * 1000)
    final = events[-1] if events else {"state": "failed", "detail": {}}
    await repo.finalise(record, final.get("detail", {}), duration_ms=elapsed,
                        failed=final.get("state") == "failed")
    await AuditRepository(session).record(
        action="investigation.run", workspace_id=principal.workspace_id,
        user_id=principal.user_id, resource_type="investigation",
        resource_id=record.reference)
    return events, record.reference


@router.post("")
async def run_investigation(
    payload: StartInvestigation,
    principal: Principal = Depends(requires(Permission.INVESTIGATION_RUN)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    """Synchronous run. Use the WebSocket for live progress on long questions."""
    try:
        events, reference = await _run_and_persist(payload, principal, session)
    except SeedDataMissing as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    final = events[-1] if events else None
    if final and final["state"] == "failed":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            final["detail"].get("error", "Investigation failed."))
    return {"reference": reference, "timeline": events,
            "result": final["detail"] if final else None}


@router.get("/history")
async def history(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    metric_key: str | None = None,
    principal: Principal = Depends(requires(Permission.INVESTIGATION_READ)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    repo = InvestigationRepository(session)
    rows = await repo.history(limit=limit, offset=offset, metric_key=metric_key)
    return {
        "total": await repo.count(),
        "investigations": [
            {"reference": r.reference, "question": r.question,
             "metric_key": r.metric_key, "status": r.status, "verdict": r.verdict,
             "headline": r.headline,
             "confidence": (r.confidence or {}).get("overall"),
             "duration_ms": r.duration_ms,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows],
    }


@router.post("/ticket")
async def websocket_ticket(
    principal: Principal = Depends(requires(Permission.INVESTIGATION_RUN)),
) -> dict:
    """Mint a 60-second credential for the streaming socket."""
    from app.core.security import WS_TICKET_TTL_SECONDS

    ticket, _ = issue_token(user_id=principal.user_id,
                            workspace_id=principal.workspace_id,
                            role=principal.role, token_type="ws")
    return {"ticket": ticket, "expires_in": WS_TICKET_TTL_SECONDS}


@router.get("/demo/scenarios")
async def demo_scenarios(principal: Principal = Depends(current_principal)) -> dict:
    """Predefined scenarios (spec section 64). These set up the question and the
    periods; every number in the answer is still computed at run time."""
    return {"scenarios": [
        {"id": "revenue-august", "question": "Why did revenue decrease in August?",
         "metric_key": "revenue",
         "current_start": "2025-08-01", "current_end": "2025-08-31",
         "comparison_start": "2025-07-01", "comparison_end": "2025-07-31",
         "dimensions": ["region", "segment", "channel", "category"]},
        {"id": "region-underperform", "question": "Which region is underperforming?",
         "metric_key": "revenue",
         "current_start": "2025-08-01", "current_end": "2025-09-30",
         "comparison_start": "2025-06-01", "comparison_end": "2025-07-31",
         "dimensions": ["region"]},
        {"id": "support-quality", "question": "Has support response time degraded?",
         "metric_key": "support_first_response_hours",
         "current_start": "2025-08-01", "current_end": "2025-08-31",
         "comparison_start": "2025-05-01", "comparison_end": "2025-05-31",
         "dimensions": ["region", "segment"]},
        {"id": "aov-shift", "question": "What happened to average order value?",
         "metric_key": "average_order_value",
         "current_start": "2025-09-01", "current_end": "2025-09-30",
         "comparison_start": "2025-08-01", "comparison_end": "2025-08-31",
         "dimensions": ["segment", "channel"]},
    ]}


@router.get("/{reference}/export")
async def export_investigation(
    reference: str,
    format: str = "markdown",
    principal: Principal = Depends(requires(Permission.REPORT_EXPORT)),
    session: AsyncSession = Depends(tenant_session),
):
    """Export a stored investigation. Qualifiers travel with every figure — a
    report stripped to its headline is worse than no report, because it removes
    exactly what tells a reader how much weight the number can bear."""
    from fastapi.responses import PlainTextResponse, Response

    from app.reports.export import build_report, to_pdf

    if format not in {"markdown", "pdf"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            "format must be 'markdown' or 'pdf'")

    record = await InvestigationRepository(session).by_reference(reference)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Investigation not found")

    payload = {
        "reference": record.reference, "question": record.question,
        "headline": record.headline, "narrative": record.narrative,
        "confidence": record.confidence, "evidence": record.evidence,
        "recommendations": record.recommendations, "verdict": record.verdict,
    }
    report = build_report(payload)
    await AuditRepository(session).record(
        action="report.export", workspace_id=principal.workspace_id,
        user_id=principal.user_id, resource_type="investigation",
        resource_id=reference)

    if format == "markdown":
        return PlainTextResponse(
            report.markdown, media_type="text/markdown; charset=utf-8",
            headers={"content-disposition":
                     f'attachment; filename="{report.filename}"'})
    return Response(
        to_pdf(report.markdown, title=report.question or reference),
        media_type="application/pdf",
        headers={"content-disposition":
                 f'attachment; filename="{reference or "investigation"}.pdf"'})


@router.get("/{reference}")
async def get_investigation(
    reference: str,
    principal: Principal = Depends(requires(Permission.INVESTIGATION_READ)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    record = await InvestigationRepository(session).by_reference(reference)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Investigation not found")
    return {
        "reference": record.reference, "question": record.question,
        "metric_key": record.metric_key, "status": record.status,
        "verdict": record.verdict, "headline": record.headline,
        "narrative": record.narrative, "confidence": record.confidence,
        "evidence": record.evidence, "recommendations": record.recommendations,
        "duration_ms": record.duration_ms,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "timeline": [{"stage": r.stage, "state": r.state,
                      "latency_ms": float(r.latency_ms) if r.latency_ms else None,
                      "detail": r.detail}
                     for r in record.runs],
    }


def _principal_from_ticket(ticket: str) -> Principal | None:
    try:
        claims = decode_token(ticket, expected_type="ws")
    except jwt.InvalidTokenError:
        return None
    if claims.workspace_id is None:
        return None
    return Principal(claims.user_id, claims.workspace_id, Role(claims.role))


@router.websocket("/stream")
async def stream_investigation(websocket: WebSocket,
                               ticket: str = Query(default="")) -> None:
    """Streams every stage transition so the client can animate progress."""
    principal = _principal_from_ticket(ticket)
    if principal is None:
        # Closed before accept: an unauthenticated client never gets a session.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION,
                              reason="Invalid or expired ticket")
        return
    if not principal.can(Permission.INVESTIGATION_RUN):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION,
                              reason="Insufficient permissions")
        return

    await websocket.accept()
    try:
        payload = StartInvestigation.model_validate(await websocket.receive_json())
    except Exception as exc:
        await websocket.send_json({"stage": "understand", "state": "failed",
                                   "detail": {"error": f"Invalid request: {exc}"}})
        await websocket.close()
        return

    async def forward(event: dict) -> None:
        await websocket.send_json(event)

    try:
        async with session_scope(principal.workspace_id) as session:
            await _run_and_persist(payload, principal, session, on_event=forward)
    except WebSocketDisconnect:
        log.info("investigation.client_disconnected")
        return
    except SeedDataMissing as exc:
        await websocket.send_json({"stage": "execute", "state": "failed",
                                   "detail": {"error": str(exc), "retryable": False}})
    except Exception:
        log.exception("investigation.stream_failed")
        await websocket.send_json({
            "stage": "complete", "state": "failed",
            "detail": {"error": "The investigation could not be completed.",
                       "retryable": True}})
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass
