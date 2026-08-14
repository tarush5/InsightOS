"""Investigation persistence: the report, its stage timeline and evidence."""
from __future__ import annotations

import secrets
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import AgentRun, Investigation


def new_reference() -> str:
    """Short human-quotable id, e.g. INV-7QK3M2. Not a security token."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "INV-" + "".join(secrets.choice(alphabet) for _ in range(6))


class InvestigationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, workspace_id: uuid.UUID, user_id: uuid.UUID,
                     question: str, metric_key: str | None) -> Investigation:
        inv = Investigation(workspace_id=workspace_id, user_id=user_id,
                            question=question, metric_key=metric_key,
                            reference=new_reference(), status="running")
        self.session.add(inv)
        await self.session.flush()
        return inv

    async def record_stage(self, investigation_id: uuid.UUID, event: dict[str, Any]) -> None:
        self.session.add(AgentRun(
            investigation_id=investigation_id,
            stage=str(event.get("stage", "")), state=str(event.get("state", "")),
            detail=_jsonable(event.get("detail", {})),
            latency_ms=event.get("latency_ms")))

    async def finalise(self, inv: Investigation, final_detail: dict[str, Any],
                       *, duration_ms: int, failed: bool = False) -> Investigation:
        if failed:
            inv.status = "failed"
            inv.headline = str(final_detail.get("error", "Investigation failed"))[:500]
        else:
            inv.status = "complete"
            inv.headline = final_detail.get("headline")
            inv.narrative = final_detail.get("narrative")
            inv.confidence = _jsonable(final_detail.get("confidence", {}))
            inv.evidence = _jsonable(final_detail.get("evidence", {}))
            inv.recommendations = _jsonable(final_detail.get("recommendations", []))
            critic = final_detail.get("critic") or {}
            inv.verdict = "approved" if critic.get("approved") else "flagged"
        inv.duration_ms = duration_ms
        await self.session.flush()
        return inv

    async def get(self, investigation_id: uuid.UUID) -> Investigation | None:
        return await self.session.scalar(
            select(Investigation).where(Investigation.id == investigation_id)
            .options(selectinload(Investigation.runs)))

    async def by_reference(self, reference: str) -> Investigation | None:
        return await self.session.scalar(
            select(Investigation).where(Investigation.reference == reference)
            .options(selectinload(Investigation.runs)))

    async def history(self, *, limit: int = 25, offset: int = 0,
                      metric_key: str | None = None) -> list[Investigation]:
        stmt = select(Investigation).order_by(Investigation.created_at.desc())
        if metric_key:
            stmt = stmt.where(Investigation.metric_key == metric_key)
        rows = await self.session.scalars(stmt.limit(limit).offset(offset))
        return list(rows)

    async def count(self) -> int:
        return int(await self.session.scalar(
            select(func.count()).select_from(Investigation)) or 0)


# Storage and the HTTP response must coerce identically; see the note in
# app/core/serialization.
from app.core.serialization import jsonable as _jsonable  # noqa: E402
