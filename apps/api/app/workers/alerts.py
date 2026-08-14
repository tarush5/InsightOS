"""
Background worker (spec section 34, batch half).

Runs the alert sweep on a schedule. Two properties matter more than throughput
here:

* **Cooldown is honoured across processes**, because it is read from and
  written to the database rather than held in worker memory. Two replicas
  sweeping the same minute cannot double-page.
* **One failing alert does not stop the sweep.** An alert whose metric was
  deleted, or whose segment no longer exists, is recorded as errored and the
  sweep continues -- otherwise a single bad definition silently disables
  monitoring for everything defined after it.

The sweep is idempotent: running it twice in the same cooldown window produces
one notification, so a retry after a crash is safe.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.alerts.engine import AlertEngine
from app.core.config import settings
from app.data.demo import evaluation_anchor, metric_series
from app.db.models import Workspace
from app.db.session import session_scope
from app.repositories.alerts import AlertRepository
from app.repositories.audit import AuditRepository
from app.semantic.registry import default_registry

log = logging.getLogger(__name__)


@dataclass(slots=True)
class SweepReport:
    checked: int = 0
    triggered: int = 0
    suppressed: int = 0
    errored: int = 0
    notifications: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"checked": self.checked, "triggered": self.triggered,
                "suppressed": self.suppressed, "errored": self.errored,
                "notifications": self.notifications, "errors": self.errors}


async def sweep_workspace(workspace_id, *, now: datetime | None = None,
                          notifier=None) -> SweepReport:
    """Evaluate every active alert in one workspace."""
    now = now or datetime.now(timezone.utc)
    report = SweepReport()
    registry = default_registry()
    engine = AlertEngine()

    async with session_scope(workspace_id) as session:
        repo = AlertRepository(session)
        audit = AuditRepository(session)
        for alert in await repo.list_active():
            report.checked += 1
            try:
                rule = repo.rule_of(alert)
                metric = registry.require_approved(rule.metric_key)
                end, _stale = evaluation_anchor(metric, now.date())
                start = end - timedelta(
                    days=max(rule.window_days + rule.comparison_days, 60))
                series = metric_series(metric, start, end, rule.segment)
                result = engine.evaluate(rule, series, now=now,
                                         last_triggered_at=alert.last_triggered_at)
            except Exception as exc:
                # A broken definition must not take the rest of the sweep with
                # it, but it must be visible: a monitor that stopped running is
                # worse than one that never existed.
                report.errored += 1
                report.errors.append({"alert_id": str(alert.id),
                                      "name": alert.name,
                                      "error": f"{type(exc).__name__}: {exc}"})
                log.warning("alert.evaluation_failed id=%s error=%s", alert.id, exc)
                await audit.record(action="alert.evaluation_failed",
                                   workspace_id=workspace_id,
                                   resource_type="alert", resource_id=str(alert.id))
                continue

            if result.suppressed_by_cooldown:
                report.suppressed += 1
                continue
            if not result.triggered:
                continue

            report.triggered += 1
            payload = {"alert_id": str(alert.id), "name": alert.name,
                       "severity": result.severity, "reason": result.reason,
                       "metric_key": rule.metric_key,
                       "workspace_id": str(workspace_id)}
            report.notifications.append(payload)

            # Marked before dispatch, deliberately. A crash between the two
            # loses one notification; the other order re-pages on every retry.
            await repo.mark_triggered(alert, now)
            await audit.record(action="alert.triggered", workspace_id=workspace_id,
                               resource_type="alert", resource_id=str(alert.id))
            if notifier is not None:
                await notifier(payload)
    return report


async def sweep_all(*, now: datetime | None = None, notifier=None) -> SweepReport:
    """Sweep every workspace. Runs one workspace at a time so a slow tenant
    cannot starve the others of a connection."""
    from sqlalchemy import select

    total = SweepReport()
    async with session_scope() as session:
        workspace_ids = list(await session.scalars(select(Workspace.id)))

    for workspace_id in workspace_ids:
        report = await sweep_workspace(workspace_id, now=now, notifier=notifier)
        total.checked += report.checked
        total.triggered += report.triggered
        total.suppressed += report.suppressed
        total.errored += report.errored
        total.notifications.extend(report.notifications)
        total.errors.extend(report.errors)

    log.info("alert.sweep_complete checked=%d triggered=%d suppressed=%d errored=%d",
             total.checked, total.triggered, total.suppressed, total.errored)
    return total


# --- arq wiring --------------------------------------------------------------
# Imported lazily so the module is testable without arq or Redis installed.

async def run_alert_sweep(ctx: dict) -> dict:
    """arq task entrypoint. Delivery receipts travel with the sweep result, so a
    run that fired three alerts and delivered none is visible in the job record
    rather than only in the logs."""
    from app.notifications import Notifier, from_alert

    notifier = Notifier(_configured_channels())

    async def deliver(payload: dict) -> None:
        await notifier.notify(from_alert(payload))

    report = await sweep_all(notifier=deliver)
    return {**report.as_dict(), "delivery": notifier.summary()}


def _configured_channels() -> list:
    """Build channels from configuration. Falls back to the log channel, which
    always works -- a sweep with no configured destination should still leave a
    trace, not silently discard the notification."""
    from app.notifications import LogChannel, WebhookChannel

    channels: list = []
    url = getattr(settings, "ALERT_WEBHOOK_URL", "") or ""
    if url:
        try:
            channels.append(WebhookChannel(url))
        except ValueError as exc:
            log.error("notify.webhook_misconfigured error=%s", exc)
    channels.append(LogChannel())
    return channels


async def _startup(ctx: dict) -> None:
    log.info("worker.startup env=%s", settings.ENV)


async def _shutdown(ctx: dict) -> None:
    from app.db.session import dispose
    await dispose()


def worker_settings():
    """Returns the arq WorkerSettings class. Called by ``arq app.workers.alerts.WorkerSettings``."""
    from arq import cron
    from arq.connections import RedisSettings

    class WorkerSettings:
        redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
        functions = [run_alert_sweep]
        on_startup = _startup
        on_shutdown = _shutdown
        # Hourly on the hour. Alert windows are measured in days, so a finer
        # schedule would re-evaluate the same window and only add load.
        cron_jobs = [cron(run_alert_sweep, minute=0)]
        max_tries = 3
        job_timeout = 300

    return WorkerSettings


try:                                  # pragma: no cover - depends on optional extra
    WorkerSettings = worker_settings()
except Exception:                     # arq or redis not installed
    WorkerSettings = None
