"""The alert sweep: isolation between alerts, idempotence, cooldown across runs."""
from datetime import datetime, timedelta, timezone

import pytest

from app.alerts.rules import compile_rule
from app.db.session import session_scope
from app.repositories.alerts import AlertRepository
from app.repositories.identity import IdentityRepository
from app.workers.alerts import sweep_all, sweep_workspace

KNOWN = {"revenue": "Revenue", "csat": "CSAT"}
NOW = datetime(2025, 9, 1, tzinfo=timezone.utc)


async def _workspace(email, name="W"):
    async with session_scope() as session:
        _, workspace, _ = await IdentityRepository(session).signup(
            email=email, password="correct-horse-battery-staple",
            full_name="", workspace_name=name)
        return workspace.id


async def _add_alert(workspace_id, text, name="A"):
    async with session_scope(workspace_id) as session:
        return (await AlertRepository(session).create(
            workspace_id=workspace_id, name=name,
            rule=compile_rule(text, KNOWN))).id


@pytest.mark.asyncio
async def test_sweep_with_no_alerts_is_a_no_op(db):
    workspace_id = await _workspace("empty@x.com")
    report = await sweep_workspace(workspace_id, now=NOW)
    assert report.checked == 0 and report.triggered == 0


@pytest.mark.asyncio
async def test_a_broken_alert_does_not_stop_the_sweep(db, monkeypatch):
    """A monitor that silently stopped running is worse than one that never
    existed, so the failure is recorded and the sweep continues.

    The healthy alert's data is stubbed rather than read from the generated
    warehouse: `seed/` is gitignored, so on a fresh clone both alerts errored
    and the test asserted the right number for the wrong reason.
    """
    import numpy as np
    import pandas as pd

    import app.workers.alerts as worker

    def fake_series(metric, start, end, segment=None):
        index = pd.date_range(end="2025-09-01", periods=60, freq="D")
        return pd.Series(np.full(60, 1000.0), index=index)

    monkeypatch.setattr(worker, "metric_series", fake_series)
    monkeypatch.setattr(worker, "evaluation_anchor",
                        lambda metric, today=None: (NOW.date(), False))

    workspace_id = await _workspace("broken@x.com")
    async with session_scope(workspace_id) as session:
        repo = AlertRepository(session)
        alert = await repo.create(workspace_id=workspace_id, name="Broken",
                                  rule=compile_rule("Alert when revenue drops 10%",
                                                    KNOWN))
        alert.rule = {**alert.rule, "metric_key": "no_such_metric"}
    await _add_alert(workspace_id, "Alert when revenue drops 10%", "Fine")

    report = await sweep_workspace(workspace_id, now=NOW)
    assert report.checked == 2
    assert report.errored == 1
    assert report.errors[0]["name"] == "Broken"


@pytest.mark.asyncio
async def test_inactive_alerts_are_skipped(db):
    workspace_id = await _workspace("inactive@x.com")
    alert_id = await _add_alert(workspace_id, "Alert when revenue drops 10%")
    async with session_scope(workspace_id) as session:
        repo = AlertRepository(session)
        await repo.set_active(await repo.get(alert_id), False)
    assert (await sweep_workspace(workspace_id, now=NOW)).checked == 0


@pytest.mark.asyncio
async def test_sweep_covers_every_workspace(db, monkeypatch):
    """`checked` counts what the sweep looked at, so it holds whether or not the
    demo warehouse exists -- but the stub keeps the test from depending on
    gitignored data either way."""
    import numpy as np
    import pandas as pd

    import app.workers.alerts as worker

    monkeypatch.setattr(worker, "metric_series", lambda *a, **k: pd.Series(
        np.full(60, 1000.0),
        index=pd.date_range(end="2025-09-01", periods=60, freq="D")))

    first = await _workspace("one@x.com", "One")
    second = await _workspace("two@x.com", "Two")
    await _add_alert(first, "Alert when revenue drops 10%")
    await _add_alert(second, "Alert when revenue drops 10%")
    assert (await sweep_all(now=NOW)).checked == 2


@pytest.mark.asyncio
async def test_notifier_receives_triggered_alerts_only(db, monkeypatch):
    """Uses a stubbed series so the test does not depend on generated seed data."""
    import numpy as np
    import pandas as pd

    import app.workers.alerts as worker

    def fake_series(metric, start, end, segment=None):
        index = pd.date_range(end="2025-09-01", periods=60, freq="D")
        return pd.Series(np.r_[np.full(53, 1000.0), np.full(7, 700.0)], index=index)

    monkeypatch.setattr(worker, "metric_series", fake_series)

    workspace_id = await _workspace("notify@x.com")
    await _add_alert(workspace_id, "Alert when revenue drops more than 10% in 7 days")

    seen = []

    async def notifier(payload):
        seen.append(payload)

    report = await sweep_workspace(workspace_id, now=NOW, notifier=notifier)
    assert report.triggered == 1
    assert len(seen) == 1 and seen[0]["severity"] in {"medium", "high", "critical"}


@pytest.mark.asyncio
async def test_second_sweep_in_the_cooldown_does_not_page_twice(db, monkeypatch):
    """Cooldown is read from the database, not worker memory, so replicas and
    retries cannot double-page."""
    import numpy as np
    import pandas as pd

    import app.workers.alerts as worker

    def fake_series(metric, start, end, segment=None):
        index = pd.date_range(end="2025-09-01", periods=60, freq="D")
        return pd.Series(np.r_[np.full(53, 1000.0), np.full(7, 700.0)], index=index)

    monkeypatch.setattr(worker, "metric_series", fake_series)

    workspace_id = await _workspace("cooldown@x.com")
    await _add_alert(workspace_id, "Alert when revenue drops more than 10% in 7 days")

    first = await sweep_workspace(workspace_id, now=NOW)
    second = await sweep_workspace(workspace_id, now=NOW + timedelta(hours=1))
    assert first.triggered == 1
    assert second.triggered == 0 and second.suppressed == 1
