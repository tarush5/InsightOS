"""Append-only audit trail (spec section 42)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, *, action: str, workspace_id: uuid.UUID | None = None,
                     user_id: uuid.UUID | None = None, resource_type: str = "",
                     resource_id: str = "") -> AuditLog:
        entry = AuditLog(action=action, workspace_id=workspace_id, user_id=user_id,
                         resource_type=resource_type, resource_id=str(resource_id))
        self.session.add(entry)
        return entry

    async def recent(self, workspace_id: uuid.UUID, limit: int = 100) -> list[AuditLog]:
        rows = await self.session.scalars(
            select(AuditLog).where(AuditLog.workspace_id == workspace_id)
            .order_by(AuditLog.occurred_at.desc()).limit(limit))
        return list(rows)
