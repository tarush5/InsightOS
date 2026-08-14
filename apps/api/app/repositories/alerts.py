"""Alert definitions and their trigger history."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.rules import AlertRule
from app.db.models import Alert


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, workspace_id: uuid.UUID, name: str,
                     rule: AlertRule) -> Alert:
        alert = Alert(workspace_id=workspace_id, name=name,
                      natural_language=rule.source_text, rule=rule.as_dict(),
                      metric_key=rule.metric_key, is_active=True)
        self.session.add(alert)
        await self.session.flush()
        return alert

    async def get(self, alert_id: uuid.UUID) -> Alert | None:
        return await self.session.scalar(select(Alert).where(Alert.id == alert_id))

    async def list_active(self) -> list[Alert]:
        rows = await self.session.scalars(
            select(Alert).where(Alert.is_active.is_(True)).order_by(Alert.created_at))
        return list(rows)

    async def list_all(self) -> list[Alert]:
        rows = await self.session.scalars(select(Alert).order_by(Alert.created_at.desc()))
        return list(rows)

    async def set_active(self, alert: Alert, active: bool) -> Alert:
        alert.is_active = active
        await self.session.flush()
        return alert

    async def mark_triggered(self, alert: Alert,
                             when: datetime | None = None) -> Alert:
        alert.last_triggered_at = when or datetime.now(timezone.utc)
        await self.session.flush()
        return alert

    @staticmethod
    def rule_of(alert: Alert) -> AlertRule:
        return AlertRule.from_dict(alert.rule)
