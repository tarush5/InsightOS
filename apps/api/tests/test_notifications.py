"""Delivery: isolation between channels, bounded retries, and a receipt either way."""
import pytest

from app.notifications.delivery import (BACKOFF_SECONDS, Channel, EmailChannel,
                                        LogChannel, Notification, Notifier, Outcome,
                                        PermanentDeliveryError, TransientDeliveryError,
                                        WebhookChannel, from_alert)

NOTE = Notification(title="Revenue drop", body="Revenue fell 18%", severity="high",
                    alert_id="a1", metric_key="revenue")


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """Retry backoff is real in production and pointless in a test."""
    async def instant(_seconds):
        return None

    monkeypatch.setattr("app.notifications.delivery.asyncio.sleep", instant)


class FlakyChannel(Channel):
    name = "flaky"

    def __init__(self, fail_times: int, error=TransientDeliveryError) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.error = error

    async def send(self, notification):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.error(f"attempt {self.calls} failed")
        return "ok"


class ExplodingChannel(Channel):
    name = "exploding"

    async def send(self, notification):
        raise RuntimeError("something unexpected")


# --- retries -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_then_succeeds():
    channel = FlakyChannel(fail_times=2)
    receipt = await channel.deliver(NOTE)
    assert receipt.delivered and receipt.attempts == 3


@pytest.mark.asyncio
async def test_retries_are_bounded():
    channel = FlakyChannel(fail_times=99)
    receipt = await channel.deliver(NOTE)
    assert not receipt.delivered
    assert channel.calls == len(BACKOFF_SECONDS)
    assert "Gave up" in receipt.detail


@pytest.mark.asyncio
async def test_a_permanent_failure_is_not_retried():
    """Retrying a 400 just sends the same malformed payload three times."""
    channel = FlakyChannel(fail_times=99, error=PermanentDeliveryError)
    receipt = await channel.deliver(NOTE)
    assert not receipt.delivered and channel.calls == 1


@pytest.mark.asyncio
async def test_an_unexpected_error_is_treated_as_permanent():
    receipt = await ExplodingChannel().deliver(NOTE)
    assert receipt.outcome is Outcome.FAILED
    assert "RuntimeError" in receipt.detail


# --- fan-out -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_broken_channel_does_not_suppress_the_others():
    """A broken webhook must not stop the email about the same incident."""
    good = LogChannel()
    receipts = await Notifier([FlakyChannel(99), good]).notify(NOTE)
    assert {r.channel: r.delivered for r in receipts} == {"flaky": False, "log": True}
    assert good.sent == [NOTE]


@pytest.mark.asyncio
async def test_every_attempt_produces_a_receipt():
    """'No alerts today' and 'delivery broke a week ago' are indistinguishable
    without one."""
    notifier = Notifier([LogChannel(), FlakyChannel(99)])
    await notifier.notify(NOTE)
    summary = notifier.summary()
    assert summary["delivered"] == 1 and summary["failed"] == 1


@pytest.mark.asyncio
async def test_no_channels_is_reported_not_silent():
    receipts = await Notifier([]).notify(NOTE)
    assert receipts[0].outcome is Outcome.SKIPPED
    assert "nothing was sent" in receipts[0].detail


@pytest.mark.asyncio
async def test_total_delivery_failure_is_logged_at_error(caplog):
    import logging

    with caplog.at_level(logging.ERROR):
        await Notifier([FlakyChannel(99)]).notify(NOTE)
    assert any("all_channels_failed" in r.message for r in caplog.records)


# --- channels ----------------------------------------------------------------

def test_webhook_requires_https():
    """The alert body names metrics and segments; plain HTTP puts that on the
    wire in clear text."""
    with pytest.raises(ValueError, match="https"):
        WebhookChannel("http://hooks.example.com/abc")


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected_delivered,expected_calls", [
    (200, True, 1),
    (204, True, 1),
    (400, False, 1),      # permanent: do not repeat a bad payload
    (404, False, 1),
    (429, False, 3),      # transient: worth retrying
    (503, False, 3),
])
async def test_webhook_status_handling(status, expected_delivered, expected_calls):
    calls = {"n": 0}

    class Response:
        status_code = status

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            calls["n"] += 1
            return Response()

    channel = WebhookChannel("https://hooks.example.com/abc", client_factory=Client)
    receipt = await channel.deliver(NOTE)
    assert receipt.delivered is expected_delivered
    assert calls["n"] == expected_calls


@pytest.mark.asyncio
async def test_email_without_a_sender_fails_loudly():
    """A channel that pretends to send is the failure this module exists to
    prevent."""
    receipt = await EmailChannel(["ops@example.com"]).deliver(NOTE)
    assert not receipt.delivered
    assert "No SMTP sender" in receipt.detail


@pytest.mark.asyncio
async def test_email_formats_and_hands_off():
    captured = {}

    async def sender(sender_address, recipients, message):
        captured.update(sender=sender_address, recipients=recipients, message=message)

    receipt = await EmailChannel(["ops@example.com"], sender=sender).deliver(NOTE)
    assert receipt.delivered
    assert "[HIGH] Revenue drop" in captured["message"]


def test_email_requires_a_recipient():
    with pytest.raises(ValueError):
        EmailChannel([])


def test_alert_payload_maps_onto_a_notification():
    note = from_alert({"name": "Revenue drop", "reason": "fell 18%",
                       "severity": "critical", "alert_id": "a1",
                       "metric_key": "revenue", "workspace_id": "w1"})
    assert note.title == "Revenue drop" and note.severity == "critical"


@pytest.mark.asyncio
async def test_the_sweep_delivers_and_reports_receipts(db, monkeypatch):
    """End to end: a firing alert produces a delivery receipt in the job result."""
    import numpy as np
    import pandas as pd

    import app.workers.alerts as worker
    from app.alerts.rules import compile_rule
    from app.db.session import session_scope
    from app.repositories.alerts import AlertRepository
    from app.repositories.identity import IdentityRepository

    def fake_series(metric, start, end, segment=None):
        index = pd.date_range(end="2025-09-01", periods=60, freq="D")
        return pd.Series(np.r_[np.full(53, 1000.0), np.full(7, 700.0)], index=index)

    monkeypatch.setattr(worker, "metric_series", fake_series)
    monkeypatch.setattr(worker, "evaluation_anchor",
                        lambda metric, today=None: (pd.Timestamp("2025-09-01").date(), False))

    async with session_scope() as session:
        _, workspace, _ = await IdentityRepository(session).signup(
            email="sweep@x.com", password="correct-horse-battery-staple",
            full_name="", workspace_name="Sweep")
        workspace_id = workspace.id
    async with session_scope(workspace_id) as session:
        await AlertRepository(session).create(
            workspace_id=workspace_id, name="Revenue drop",
            rule=compile_rule("Alert when revenue drops more than 10% in 7 days",
                              {"revenue": "Revenue"}))

    result = await worker.run_alert_sweep({})
    assert result["triggered"] == 1
    assert result["delivery"]["delivered"] >= 1
