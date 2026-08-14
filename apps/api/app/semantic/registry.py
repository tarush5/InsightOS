"""
Business semantic layer (spec section 13).

A metric is a governed, versioned object -- not a string the model invents at
query time. The agent must resolve a metric through this registry before it is
allowed to build SQL for it, which is what stops "revenue" silently meaning
gross bookings in one answer and net of refunds in the next.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class MetricStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class Aggregation(StrEnum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    RATIO = "ratio"


@dataclass(slots=True)
class Dimension:
    name: str
    column: str
    table: str
    description: str = ""
    is_temporal: bool = False


@dataclass(slots=True)
class MetricDefinition:
    key: str
    label: str
    description: str
    aggregation: Aggregation
    expression: str                 # SQL fragment, validated on registration
    base_table: str
    date_column: str
    dimensions: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    unit: str = "count"
    higher_is_better: bool = True
    version: int = 1
    status: MetricStatus = MetricStatus.DRAFT
    owner: str = "unassigned"
    approved_by: str | None = None
    approved_on: date | None = None
    notes: str = ""

    @property
    def is_usable(self) -> bool:
        return self.status is MetricStatus.APPROVED

    def as_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "description": self.description,
            "aggregation": str(self.aggregation), "expression": self.expression,
            "base_table": self.base_table, "date_column": self.date_column,
            "dimensions": self.dimensions, "filters": self.filters, "unit": self.unit,
            "higher_is_better": self.higher_is_better, "version": self.version,
            "status": str(self.status), "owner": self.owner,
            "approved_by": self.approved_by,
            "approved_on": self.approved_on.isoformat() if self.approved_on else None,
            "notes": self.notes,
        }


class MetricRegistry:
    """In-process registry. The persistent copy lives in the `metrics` table;
    this is the read-through cache the agent tools talk to."""

    def __init__(self) -> None:
        self._metrics: dict[str, MetricDefinition] = {}
        self._dimensions: dict[str, Dimension] = {}

    # -- registration ---------------------------------------------------------

    def register(self, metric: MetricDefinition, *, replace: bool = False) -> MetricDefinition:
        existing = self._metrics.get(metric.key)
        if existing and not replace:
            metric.version = existing.version + 1
        self._validate(metric)
        self._metrics[metric.key] = metric
        return metric

    def register_dimension(self, dim: Dimension) -> None:
        self._dimensions[dim.name] = dim

    def approve(self, key: str, approver: str) -> MetricDefinition:
        m = self._metrics[key]
        m.status = MetricStatus.APPROVED
        m.approved_by = approver
        m.approved_on = date.today()
        return m

    def deprecate(self, key: str) -> MetricDefinition:
        m = self._metrics[key]
        m.status = MetricStatus.DEPRECATED
        return m

    # -- lookup ---------------------------------------------------------------

    def get(self, key: str) -> MetricDefinition | None:
        return self._metrics.get(key)

    def require_approved(self, key: str) -> MetricDefinition:
        m = self._metrics.get(key)
        if m is None:
            raise KeyError(
                f"Metric '{key}' is not defined. Available: {sorted(self._metrics)}"
            )
        if not m.is_usable:
            raise PermissionError(
                f"Metric '{key}' has status '{m.status}' and cannot be used in an "
                "analysis. Ask a metric owner to approve it first."
            )
        return m

    def all(self, *, approved_only: bool = False) -> list[MetricDefinition]:
        vals = list(self._metrics.values())
        return [m for m in vals if m.is_usable] if approved_only else vals

    def dimension(self, name: str) -> Dimension | None:
        return self._dimensions.get(name)

    def search(self, text: str, *, limit: int = 5) -> list[MetricDefinition]:
        """Lexical match used to ground a natural-language question in real metrics.
        Deliberately simple and auditable; the vector index in `rag/` handles the
        fuzzy case and defers to this for the final resolution."""
        tokens = set(re.findall(r"[a-z]+", text.lower()))
        scored = []
        for m in self._metrics.values():
            hay = f"{m.key} {m.label} {m.description}".lower()
            words = set(re.findall(r"[a-z]+", hay))
            overlap = len(tokens & words)
            if m.key.replace("_", " ") in text.lower():
                overlap += 3
            if overlap:
                scored.append((overlap, m))
        scored.sort(key=lambda t: (-t[0], t[1].key))
        return [m for _, m in scored[:limit]]

    # -- validation -----------------------------------------------------------

    _FORBIDDEN = re.compile(
        r"\b(drop|delete|insert|update|truncate|alter|grant|create|copy)\b", re.I
    )

    def _validate(self, m: MetricDefinition) -> None:
        if self._FORBIDDEN.search(m.expression):
            raise ValueError(
                f"Metric '{m.key}' expression contains a write operation. Metric "
                "expressions must be pure projections."
            )
        if ";" in m.expression:
            raise ValueError(f"Metric '{m.key}' expression must be a single expression.")
        if m.aggregation is Aggregation.RATIO and "/" not in m.expression:
            raise ValueError(f"Ratio metric '{m.key}' must contain a division.")


def default_registry() -> MetricRegistry:
    """Seed definitions matching the generated demo warehouse."""
    r = MetricRegistry()

    for d in [
        Dimension("region", "region", "orders", "Sales region the order was booked in"),
        Dimension("segment", "segment", "orders", "Customer size segment"),
        Dimension("channel", "channel", "orders", "Acquisition/sales channel"),
        Dimension("category", "category", "orders", "Product category"),
        Dimension("order_date", "order_date", "orders", "Order booking date", is_temporal=True),
    ]:
        r.register_dimension(d)

    metrics = [
        MetricDefinition(
            key="revenue", label="Net Revenue",
            description="Completed order value, net of discounts. Cancelled orders "
                        "are excluded. Refunds are handled by net_revenue_after_refunds.",
            aggregation=Aggregation.SUM,
            expression="SUM(orders.total_amount)",
            base_table="orders", date_column="order_date",
            dimensions=["region", "segment", "channel", "category"],
            filters=["orders.status = 'completed'"],
            unit="currency", owner="finance@example.com",
        ),
        MetricDefinition(
            key="order_count", label="Orders",
            description="Number of completed orders.",
            aggregation=Aggregation.COUNT,
            expression="COUNT(orders.order_id)",
            base_table="orders", date_column="order_date",
            dimensions=["region", "segment", "channel", "category"],
            filters=["orders.status = 'completed'"], unit="count",
            owner="revops@example.com",
        ),
        MetricDefinition(
            key="average_order_value", label="Average Order Value",
            description="Net revenue divided by completed order count.",
            aggregation=Aggregation.RATIO,
            expression="SUM(orders.total_amount) / NULLIF(COUNT(orders.order_id), 0)",
            base_table="orders", date_column="order_date",
            dimensions=["region", "segment", "channel"],
            filters=["orders.status = 'completed'"], unit="currency",
            owner="revops@example.com",
        ),
        MetricDefinition(
            key="churn_rate", label="Customer Churn Rate",
            description="Customers whose churn_date falls in the period, divided by "
                        "customers active at the start of the period.",
            aggregation=Aggregation.RATIO,
            expression="COUNT(DISTINCT CASE WHEN customers.churn_date IS NOT NULL "
                       "THEN customers.customer_id END) / "
                       "NULLIF(COUNT(DISTINCT customers.customer_id), 0)",
            base_table="customers", date_column="churn_date",
            dimensions=["region", "segment", "channel"],
            unit="percent", higher_is_better=False, owner="cs@example.com",
        ),
        MetricDefinition(
            key="support_first_response_hours", label="First Response Time",
            description="Mean hours from ticket creation to first agent response.",
            aggregation=Aggregation.AVG,
            expression="AVG(support_tickets.first_response_hours)",
            base_table="support_tickets", date_column="created_date",
            dimensions=["region", "segment", "priority", "category"],
            unit="hours", higher_is_better=False, owner="support@example.com",
        ),
        MetricDefinition(
            key="csat", label="Customer Satisfaction",
            description="Mean CSAT score (1-5) across resolved tickets.",
            aggregation=Aggregation.AVG,
            expression="AVG(support_tickets.csat_score)",
            base_table="support_tickets", date_column="created_date",
            dimensions=["region", "segment", "priority"],
            unit="score", owner="support@example.com",
        ),
        MetricDefinition(
            key="discount_rate", label="Effective Discount Rate",
            description="Discount amount as a share of gross order value.",
            aggregation=Aggregation.RATIO,
            expression="SUM(orders.discount_amount) / "
                       "NULLIF(SUM(orders.total_amount + orders.discount_amount), 0)",
            base_table="orders", date_column="order_date",
            dimensions=["region", "segment", "category"],
            filters=["orders.status = 'completed'"],
            unit="percent", higher_is_better=False, owner="finance@example.com",
        ),
    ]
    for m in metrics:
        r.register(m, replace=True)
        r.approve(m.key, approver="seed@insightos.local")
    return r
