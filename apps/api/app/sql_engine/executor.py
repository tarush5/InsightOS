"""
Execution of validated SQL against a real warehouse.

The validator decides whether a statement is *allowed*. This module decides what
it is *able* to do, and the two are deliberately independent. Validation is a
parser, and parsers have bugs; a dialect quirk that slips one write statement
past the AST allowlist should still fail at the database. So every query runs
inside a read-only transaction with a statement timeout, on a connection whose
role should not hold write grants in the first place.

Three independent limits, because each fails differently:

* **Read-only transaction** — stops writes the parser missed.
* **Statement timeout** — stops a query that planned badly from holding a
  connection open until someone notices.
* **Row cap** — stops a correct, fast query from returning a result set large
  enough to exhaust API memory. Enforced on fetch as well as by the injected
  LIMIT, since a LIMIT inside a CTE does not bound the outer result.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings
from app.datasources.secrets import redact
from app.sql_engine.validator import SQLValidator, ValidationResult

log = logging.getLogger(__name__)


class QueryRejected(Exception):
    """Validation failed. Carries the findings so the caller can repair the SQL."""

    def __init__(self, result: ValidationResult) -> None:
        super().__init__(result.error_summary)
        self.result = result


class QueryFailed(Exception):
    """The database rejected or could not complete the statement."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ResultTooLarge(QueryFailed):
    def __init__(self, cap: int) -> None:
        super().__init__(
            f"The query returned more than {cap:,} rows. Aggregate it further or "
            "narrow the date range — InsightOS analyses summaries, not exports.")


@dataclass(slots=True)
class QueryResult:
    frame: pd.DataFrame
    sql: str                       # the rewritten statement that actually ran
    row_count: int
    duration_ms: float
    truncated: bool = False
    validation: ValidationResult | None = None
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"sql": self.sql, "row_count": self.row_count,
                "duration_ms": round(self.duration_ms, 2),
                "truncated": self.truncated, "warnings": self.warnings,
                "columns": list(self.frame.columns)}


class SQLExecutor:
    """Validates, rewrites and executes a single read-only statement.

    One executor wraps one data source. The engine is pooled and long-lived; the
    per-query safety settings are applied inside each transaction so they cannot
    leak between callers sharing a pooled connection.
    """

    def __init__(self, dsn: str, *, validator: SQLValidator,
                 statement_timeout_ms: int | None = None,
                 max_rows: int | None = None,
                 pool_size: int = 5,
                 engine: AsyncEngine | None = None) -> None:
        self.validator = validator
        self.statement_timeout_ms = statement_timeout_ms or settings.SQL_STATEMENT_TIMEOUT_MS
        self.max_rows = max_rows or settings.SQL_MAX_RESULT_ROWS
        self._dsn = dsn
        if engine:
            self._engine = engine
        elif "sqlite" in dsn:
            self._engine = create_async_engine(dsn, pool_pre_ping=True)
        else:
            self._engine = create_async_engine(
                dsn, pool_size=pool_size, max_overflow=2, pool_pre_ping=True,
                pool_recycle=1800)

    @property
    def dialect(self) -> str:
        return self._engine.dialect.name

    def __repr__(self) -> str:  # never leak the password in a traceback
        return f"<SQLExecutor {redact(self._dsn)}>"

    async def dispose(self) -> None:
        await self._engine.dispose()

    # --- safety settings --------------------------------------------------
    async def _harden(self, connection) -> list[str]:
        """Apply per-transaction limits. Returns warnings for anything the
        backend could not enforce, so a weaker backend is visible rather than
        assumed safe."""
        warnings: list[str] = []
        name = self._engine.dialect.name

        if name == "postgresql":
            await connection.execute(
                text(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}"))
            await connection.execute(text("SET LOCAL TRANSACTION READ ONLY"))
            # Bounds a runaway join before the timeout has to catch it.
            await connection.execute(
                text(f"SET LOCAL work_mem = '{max(4, 64)}MB'"))
        elif name == "sqlite":
            # query_only is the closest equivalent; SQLite has no per-statement
            # timeout, so the row cap and the caller's own timeout carry that
            # load. Saying so beats implying parity.
            await connection.execute(text("PRAGMA query_only = ON"))
            warnings.append(
                "SQLite enforces read-only but has no statement timeout; a "
                "pathological query is bounded only by the row cap.")
        else:
            warnings.append(
                f"No read-only or timeout enforcement is implemented for "
                f"'{name}'. The SQL validator is the only control in effect, "
                "which is weaker than intended.")
        return warnings

    # --- execution --------------------------------------------------------
    async def execute(self, sql: str, *, params: dict[str, Any] | None = None
                      ) -> QueryResult:
        validation = self.validator.validate(sql)
        if not validation.ok:
            raise QueryRejected(validation)

        statement = validation.safe_sql or sql
        started = time.perf_counter()

        async with self._engine.connect() as connection:
            async with connection.begin():
                warnings = await self._harden(connection)
                try:
                    cursor = await connection.execute(text(statement), params or {})
                    # fetchmany with a cap+1 probe: a query whose LIMIT sat
                    # inside a CTE can still produce an unbounded outer result,
                    # and materialising it would be the failure we are avoiding.
                    rows = cursor.fetchmany(self.max_rows + 1)
                    columns = list(cursor.keys())
                except DBAPIError as exc:
                    # The database answered with an error: a timeout, a denied
                    # permission, a rejected write. Translate it into something
                    # the caller can act on.
                    #
                    # This clause has to come first: DBAPIError subclasses
                    # StatementError, so the order below would swallow every
                    # database error into the re-raise branch and no translation
                    # would ever run.
                    log.warning("sql.failed error=%s: %s", type(exc).__name__, exc)
                    raise self._translate(exc) from exc
                except StatementError:
                    # Never reached the database. A binding or compilation
                    # failure is our bug, not a database condition, and
                    # translating it would bury a defect in generated SQL behind
                    # "the query could not be completed".
                    log.exception("sql.statement_error sql=%s", statement)
                    raise
                except Exception as exc:
                    log.warning("sql.failed error=%s: %s", type(exc).__name__, exc)
                    raise self._translate(exc) from exc

        truncated = len(rows) > self.max_rows
        if truncated:
            rows = rows[: self.max_rows]
            warnings.append(
                f"Result truncated to {self.max_rows:,} rows. Figures computed "
                "from it describe the truncated set, not the full query.")

        frame = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
        duration = (time.perf_counter() - started) * 1000
        log.info("sql.executed rows=%d ms=%.1f tables=%s",
                 len(frame), duration, ",".join(validation.referenced_tables))

        return QueryResult(frame=frame, sql=statement, row_count=len(frame),
                           duration_ms=duration, truncated=truncated,
                           validation=validation, warnings=warnings)

    def _translate(self, exc: Exception) -> QueryFailed:
        """Database errors reach users, so they must be actionable and must not
        echo the DSN or the server's internal detail."""
        message = str(exc).lower()
        if "timeout" in message or "canceling statement" in message:
            return QueryFailed(
                f"The query exceeded the {self.statement_timeout_ms}ms limit. "
                "Narrow the date range or aggregate before joining.",
                retryable=False)
        if "read-only" in message or "readonly" in message or "query_only" in message:
            return QueryFailed(
                "That statement tried to modify data. InsightOS connects "
                "read-only.", retryable=False)
        if "permission denied" in message or "not authorized" in message:
            return QueryFailed(
                "The data source credential lacks permission for that table. "
                "Grant SELECT to the InsightOS role.", retryable=False)
        if "does not exist" in message or "no such table" in message:
            return QueryFailed(
                "A referenced table or column does not exist in the source. "
                "Re-crawl the schema so the catalog matches the warehouse.",
                retryable=False)
        if any(w in message for w in ("connection", "could not connect", "closed")):
            return QueryFailed(
                "Could not reach the data source. Check that it is running and "
                "reachable from the API.", retryable=True)
        return QueryFailed("The query could not be completed.", retryable=False)

    async def healthcheck(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception as exc:
            log.warning("datasource.unreachable target=%s error=%s",
                        redact(self._dsn), type(exc).__name__)
            return False
