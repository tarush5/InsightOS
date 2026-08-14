"""
The real data provider.

This is the swap the whole architecture was arranged around. The orchestrator,
alert engine and causal estimator all take a callable with the signature
``(metric, start, end) -> DataFrame`` and cannot tell whether the rows came from
a CSV or a warehouse. Replacing ``demo_data_provider`` with this class changes
nothing downstream — that was the point of keeping the boundary narrow.

SQL is generated from the governed metric definition rather than by a model.
That is a deliberate limitation, not an oversight: the semantic layer already
holds the table, the measure and the aggregation, so generating from it is both
correct by construction and auditable. Text-to-SQL belongs upstream of this, as
a way to *choose* a metric, with the validator as the gate — not as a way to
invent the arithmetic behind an approved one.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd
import sqlglot
from sqlglot import exp

from app.sql_engine.executor import QueryRejected, QueryResult, SQLExecutor

log = logging.getLogger(__name__)


class MetricNotQueryable(ValueError):
    """The metric's definition does not describe something this source can answer."""


class SQLDataProvider:
    """Builds and runs the query behind one metric, for one data source."""

    def __init__(self, executor: SQLExecutor, *, dialect: str | None = None) -> None:
        self.executor = executor
        self.dialect = dialect or executor.dialect

    def available_dimensions(self, metric, requested: list[str] | None = None
                             ) -> tuple[list[str], list[str]]:
        """Split requested dimensions into (available, missing).

        A metric definition can outlive the schema it describes. Failing the
        whole investigation because one declared breakdown no longer exists
        would make the product brittle in the ordinary case of a renamed
        column, so the missing ones are dropped -- and named in the result, so
        the omission is visible rather than silent.
        """
        wanted = list(requested if requested is not None
                      else (getattr(metric, "dimensions", None) or []))
        catalog = getattr(self.executor.validator, "catalog", None)
        spec = catalog.get(metric.base_table) if catalog else None
        if spec is None:
            return wanted, []
        known = {c.lower() for c in spec.columns}
        available = [d for d in wanted if d.lower() in known]
        missing = [d for d in wanted if d.lower() not in known]
        return available, missing

    async def fetch(self, metric, start: date, end: date, *,
                    dimensions: list[str] | None = None) -> QueryResult:
        available, missing = self.available_dimensions(metric, dimensions)
        sql = self.build_sql(metric, start, end, dimensions=available)
        try:
            result = await self.executor.execute(sql)
        except QueryRejected as exc:
            # The generator produced SQL its own validator rejects. That is a
            # bug in the metric definition or the generator, never something to
            # retry, so it says which rule fired rather than failing generically.
            raise MetricNotQueryable(
                f"The query generated for metric '{metric.key}' failed "
                f"validation: {exc.result.error_summary}") from None

        if missing:
            result.warnings.append(
                f"Metric '{metric.key}' declares dimension(s) "
                f"{', '.join(missing)} that the source does not have, so the "
                "breakdown excludes them. Re-crawl the schema or update the "
                "metric definition.")
        return result

    # --- generation -------------------------------------------------------
    def build_sql(self, metric, start: date, end: date,
                  dimensions: list[str] | None = None) -> str:
        """Compose the statement as an AST rather than by string formatting.

        The metric's ``expression`` and ``filters`` are SQL fragments authored by
        an analyst and validated on registration. They are re-parsed here rather
        than concatenated: parsing turns a fragment into a tree that has to be a
        single expression, so a fragment carrying a second statement or a stray
        closing paren fails here instead of composing into something valid and
        unintended. Rendering through sqlglot then quotes identifiers for the
        target dialect.

        The date bounds are rendered as typed literals rather than bound
        parameters. That was not the first choice: parameters are the default
        answer, and the first version used them. But the statement is re-parsed
        and re-rendered by the validator before it executes, and a placeholder
        does not survive that round trip -- sqlglot re-emits it in the target
        dialect's own style (`?`, `%(name)s`), which no longer matches what
        SQLAlchemy's text() binds. Rather than paper over that with string
        fixups, the bounds are rendered from `date` objects, which are typed by
        Pydantic at the API boundary and cannot carry a quote character.
        `_date_literal` enforces the type so a string can never reach it.
        """
        table = getattr(metric, "base_table", None)
        date_column = getattr(metric, "date_column", None)
        if not table or not date_column:
            raise MetricNotQueryable(
                f"Metric '{metric.key}' has no base table or date column, so no "
                "query can be generated from it.")

        group_columns = list(dimensions if dimensions is not None
                             else (getattr(metric, "dimensions", None) or []))

        select: list[exp.Expression] = [
            exp.column(date_column).as_(date_column),
            *[exp.column(d).as_(d) for d in group_columns],
            self._measure(metric).as_(metric.key),
        ]

        where = exp.and_(
            exp.GTE(this=exp.column(date_column),
                    expression=self._date_literal(start, "start")),
            exp.LTE(this=exp.column(date_column),
                    expression=self._date_literal(end, "end")),
        )
        for fragment in (getattr(metric, "filters", None) or []):
            where = exp.and_(where, self._condition(fragment, metric.key))

        query = (exp.select(*select)
                 .from_(exp.to_table(table))
                 .where(where)
                 .group_by(*[exp.column(c) for c in [date_column, *group_columns]])
                 .order_by(exp.column(date_column)))
        return query.sql(dialect=self.dialect)

    @staticmethod
    def _date_literal(value: object, name: str) -> exp.Expression:
        """Render a date bound. Rejects anything that is not a real date.

        This type check is the control that makes literal rendering safe, so it
        raises rather than coercing: a caller passing a string here would have
        found a way to put arbitrary text into a SQL statement.
        """
        import datetime as _dt

        if isinstance(value, _dt.datetime):
            value = value.date()
        if not isinstance(value, _dt.date):
            raise MetricNotQueryable(
                f"The '{name}' bound must be a date, not {type(value).__name__}.")
        return exp.Literal.string(value.isoformat())

    def _measure(self, metric) -> exp.Expression:
        """The metric expression, parsed. Already-aggregated expressions pass
        through; a bare column is wrapped in the declared aggregation."""
        expression = (getattr(metric, "expression", "") or "").strip()
        if not expression:
            raise MetricNotQueryable(
                f"Metric '{metric.key}' has no expression to compute.")
        try:
            parsed = sqlglot.parse_one(expression, dialect=self.dialect)
        except Exception as exc:
            raise MetricNotQueryable(
                f"Metric '{metric.key}' has an expression that is not valid "
                f"SQL: {exc}") from None
        if parsed is None:
            raise MetricNotQueryable(
                f"Metric '{metric.key}' has an empty expression.")

        if list(parsed.find_all(exp.AggFunc)):
            return parsed

        aggregation = str(getattr(getattr(metric, "aggregation", "sum"), "value",
                                  getattr(metric, "aggregation", "sum"))).lower()
        match aggregation:
            case "sum":
                return exp.Sum(this=parsed)
            case "avg" | "mean":
                return exp.Avg(this=parsed)
            case "min":
                return exp.Min(this=parsed)
            case "max":
                return exp.Max(this=parsed)
            case "count":
                return exp.Count(this=parsed)
            case "count_distinct":
                return exp.Count(this=exp.Distinct(expressions=[parsed]))
            case "ratio":
                # A ratio metric whose expression is a bare column is a broken
                # definition, not something to guess a numerator for.
                raise MetricNotQueryable(
                    f"Metric '{metric.key}' is declared as a ratio but its "
                    "expression is a single column. A ratio must state its own "
                    "numerator and denominator.")
            case _:
                raise MetricNotQueryable(
                    f"Aggregation '{aggregation}' is not supported.")

    def _condition(self, fragment: str, metric_key: str) -> exp.Expression:
        try:
            parsed = sqlglot.parse_one(fragment, dialect=self.dialect)
        except Exception as exc:
            raise MetricNotQueryable(
                f"Metric '{metric_key}' has a filter that is not valid SQL: "
                f"{exc}") from None
        if parsed is None or isinstance(parsed, (exp.Select, exp.Union)):
            raise MetricNotQueryable(
                f"Metric '{metric_key}' has a filter that is a statement rather "
                "than a condition.")
        return exp.paren(parsed)

    # --- adapter ----------------------------------------------------------
    def as_data_provider(self):
        """Return the ``(metric, start, end) -> DataFrame`` callable the
        orchestrator expects, so it is a drop-in for ``demo_data_provider``."""

        async def provider(metric, start: date, end: date) -> pd.DataFrame:
            result = await self.fetch(metric, start, end)
            if result.truncated:
                log.warning("provider.truncated metric=%s rows=%d",
                            metric.key, result.row_count)
            return result.frame

        return provider
