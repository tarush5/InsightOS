"""
Notification delivery (spec section 25, delivery half).

The alert sweep decides *whether* to notify. This decides whether the
notification arrived, which is a different problem with different failure
modes — and the one that determines if the alerting feature is real. An alert
engine whose messages silently fail to send is indistinguishable from one that
never fires.

Three properties the design turns on:

* **A channel that fails does not stop the others.** A broken webhook must not
  suppress the email about the same incident.
* **Every attempt produces a receipt**, success or failure, with the reason.
  "No alerts today" and "delivery has been broken for a week" look identical
  without one.
* **Retries are bounded and only for transient failures.** Retrying a 400 just
  sends the same malformed payload four times; retrying a 503 is worth doing.
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (0.5, 2.0, 5.0)


class Outcome(StrEnum):
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class Notification:
    title: str
    body: str
    severity: str = "medium"
    alert_id: str = ""
    workspace_id: str = ""
    metric_key: str = ""
    link: str = ""

    def as_dict(self) -> dict:
        return {"title": self.title, "body": self.body, "severity": self.severity,
                "alert_id": self.alert_id, "workspace_id": self.workspace_id,
                "metric_key": self.metric_key, "link": self.link}


@dataclass(slots=True)
class Receipt:
    channel: str
    outcome: Outcome
    attempts: int = 1
    detail: str = ""
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def delivered(self) -> bool:
        return self.outcome is Outcome.DELIVERED

    def as_dict(self) -> dict:
        return {"channel": self.channel, "outcome": str(self.outcome),
                "attempts": self.attempts, "detail": self.detail, "at": self.at}


class TransientDeliveryError(Exception):
    """Worth retrying: a timeout, a 5xx, a refused connection."""


class PermanentDeliveryError(Exception):
    """Not worth retrying: a 4xx, a malformed address, a missing credential."""


class Channel(ABC):
    name: str = "channel"

    @abstractmethod
    async def send(self, notification: Notification) -> str:
        """Deliver, or raise Transient/PermanentDeliveryError. Returns a detail
        string for the receipt."""

    async def deliver(self, notification: Notification) -> Receipt:
        last = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                detail = await self.send(notification)
                return Receipt(self.name, Outcome.DELIVERED, attempt, detail)
            except PermanentDeliveryError as exc:
                # Retrying would send the same broken payload again.
                return Receipt(self.name, Outcome.FAILED, attempt, str(exc))
            except TransientDeliveryError as exc:
                last = str(exc)
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(BACKOFF_SECONDS[attempt - 1])
            except Exception as exc:
                # An unexpected error is treated as permanent: retrying an
                # unknown failure three times mostly multiplies the damage.
                log.exception("notify.unexpected channel=%s", self.name)
                return Receipt(self.name, Outcome.FAILED, attempt,
                               f"{type(exc).__name__}: {exc}")
        return Receipt(self.name, Outcome.FAILED, MAX_ATTEMPTS,
                       f"Gave up after {MAX_ATTEMPTS} attempts: {last}")


class LogChannel(Channel):
    """Always available. Makes delivery observable with nothing configured."""

    name = "log"

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    async def send(self, notification: Notification) -> str:
        self.sent.append(notification)
        log.info("notify severity=%s alert=%s title=%s",
                 notification.severity, notification.alert_id, notification.title)
        return "written to the application log"


class WebhookChannel(Channel):
    """POSTs JSON. Works with Slack, Teams and anything else that accepts a hook."""

    name = "webhook"

    def __init__(self, url: str, *, timeout: float = 10.0,
                 client_factory=None) -> None:
        if not url.startswith("https://"):
            # Plain HTTP would put the alert body, which can name metrics and
            # segments, on the wire in clear text.
            raise ValueError("Webhook URLs must be https.")
        self.url = url
        self.timeout = timeout
        self._client_factory = client_factory

    async def send(self, notification: Notification) -> str:
        import httpx

        factory = self._client_factory or (
            lambda: httpx.AsyncClient(timeout=self.timeout))
        try:
            async with factory() as client:
                response = await client.post(
                    self.url, json=notification.as_dict(),
                    headers={"content-type": "application/json"})
        except Exception as exc:
            raise TransientDeliveryError(f"{type(exc).__name__}: {exc}") from exc

        if 200 <= response.status_code < 300:
            return f"HTTP {response.status_code}"
        if response.status_code in (408, 425, 429) or response.status_code >= 500:
            raise TransientDeliveryError(f"HTTP {response.status_code}")
        raise PermanentDeliveryError(
            f"HTTP {response.status_code}. Check the webhook URL and payload "
            "format expected by the receiver.")


class EmailChannel(Channel):
    """SMTP delivery.

    Not wired to a provider: it formats and hands off to an injected sender, so
    the transport is a deployment choice. The default sender raises, because a
    channel that pretends to send is the failure this module exists to prevent.
    """

    name = "email"

    def __init__(self, recipients: list[str], *, sender=None,
                 from_address: str = "alerts@insightos.local") -> None:
        if not recipients:
            raise ValueError("EmailChannel needs at least one recipient.")
        self.recipients = recipients
        self.from_address = from_address
        self._sender = sender

    async def send(self, notification: Notification) -> str:
        if self._sender is None:
            raise PermanentDeliveryError(
                "No SMTP sender is configured. Email delivery is formatted but "
                "not transported; inject a sender or use the webhook channel.")
        message = (f"Subject: [{notification.severity.upper()}] {notification.title}\n"
                   f"From: {self.from_address}\n"
                   f"To: {', '.join(self.recipients)}\n\n"
                   f"{notification.body}\n"
                   + (f"\n{notification.link}\n" if notification.link else ""))
        try:
            await self._sender(self.from_address, self.recipients, message)
        except Exception as exc:
            raise TransientDeliveryError(f"{type(exc).__name__}: {exc}") from exc
        return f"sent to {len(self.recipients)} recipient(s)"


class Notifier:
    """Fans a notification out across channels, isolating their failures."""

    def __init__(self, channels: list[Channel] | None = None) -> None:
        # `None` means "not configured" and gets the log channel so delivery is
        # at least observable. An explicitly empty list means "send nowhere",
        # and is reported as skipped rather than quietly substituted -- `or`
        # here would collapse the two and hide a misconfiguration.
        self.channels = [LogChannel()] if channels is None else list(channels)
        self.receipts: list[Receipt] = []

    async def notify(self, notification: Notification) -> list[Receipt]:
        if not self.channels:
            return [Receipt("none", Outcome.SKIPPED, 0,
                            "No channels are configured, so nothing was sent.")]

        results = await asyncio.gather(
            *(channel.deliver(notification) for channel in self.channels),
            return_exceptions=True)

        receipts: list[Receipt] = []
        for channel, result in zip(self.channels, results, strict=True):
            if isinstance(result, BaseException):
                receipts.append(Receipt(channel.name, Outcome.FAILED, 1,
                                        f"{type(result).__name__}: {result}"))
            else:
                receipts.append(result)

        self.receipts.extend(receipts)
        delivered = sum(1 for r in receipts if r.delivered)
        if delivered == 0:
            # Loud, because this is the state that makes alerting a lie.
            log.error("notify.all_channels_failed alert=%s detail=%s",
                      notification.alert_id,
                      json.dumps([r.as_dict() for r in receipts]))
        return receipts

    def summary(self) -> dict:
        return {"total": len(self.receipts),
                "delivered": sum(1 for r in self.receipts if r.delivered),
                "failed": sum(1 for r in self.receipts if not r.delivered),
                "receipts": [r.as_dict() for r in self.receipts[-20:]]}


def from_alert(payload: dict) -> Notification:
    """Build a notification from what the alert sweep produces."""
    return Notification(
        title=payload.get("name", "Alert triggered"),
        body=payload.get("reason", ""),
        severity=payload.get("severity", "medium"),
        alert_id=str(payload.get("alert_id", "")),
        workspace_id=str(payload.get("workspace_id", "")),
        metric_key=payload.get("metric_key", ""))
