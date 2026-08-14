"""
Ad-hoc query routes (spec sections 17-19).

This is the escape hatch from the semantic layer: a question with no governed
metric behind it. That makes it the most dangerous endpoint in the product, so
it is also the most constrained one.

`/explain` generates and validates without executing. It exists because the
right first response to "here is some SQL a model wrote" is to read it, and
because it lets a caller see the repair attempts without touching the warehouse.

Answers from here are not equivalent to a metric-backed investigation, and the
response says so. A metric has an owner, an approval and a version; ad-hoc SQL
has a model's best guess at what the question meant, which is a materially
weaker claim and should not be presented as the same thing.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, requires, tenant_session
from app.core.security import Permission
from app.datasources.registry import DataSourceUnavailable, get_registry
from app.repositories.audit import AuditRepository
from app.repositories.datasources import DataSourceRepository
from app.sql_engine.executor import QueryFailed, QueryRejected
from app.sql_engine.generator import SQLGenerator

router = APIRouter()

PROVENANCE_NOTE = (
    "Generated from your question by a language model and checked by the SQL "
    "validator. Unlike a governed metric it has no owner, no approval and no "
    "version — read the SQL before acting on the numbers.")


class AskRequest(BaseModel):
    question: str = Field(min_length=5, max_length=500)
    data_source_id: uuid.UUID
    hints: str = Field(default="", max_length=1000)
    max_attempts: int = Field(default=3, ge=1, le=5)


async def _executor(session: AsyncSession, principal: Principal,
                    data_source_id: uuid.UUID):
    repo = DataSourceRepository(session)
    source = await repo.get(data_source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found")

    catalog = await repo.catalog_for(data_source_id)
    if not catalog.tables:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No schema has been recorded for this source. Crawl it first — the "
            "generator is given the crawled catalog and nothing else, so an "
            "empty catalog can only produce rejected queries.")
    try:
        executor = await get_registry().executor(
            workspace_id=principal.workspace_id, data_source_id=data_source_id,
            secret_ref=source.secret_ref, catalog=catalog)
    except DataSourceUnavailable as exc:
        raise HTTPException(status.HTTP_424_FAILED_DEPENDENCY, str(exc))
    return executor


def _generation_error(result) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": "no_valid_query",
                "message": result.reason,
                "degraded": result.degraded,
                "attempts": [a.as_dict() for a in result.attempts]})


@router.post("/explain")
async def explain(
    payload: AskRequest,
    principal: Principal = Depends(requires(Permission.SQL_EXECUTE)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    """Generate and validate SQL without running it."""
    executor = await _executor(session, principal, payload.data_source_id)
    generator = SQLGenerator(executor.validator, max_attempts=payload.max_attempts)
    result = await generator.generate(payload.question, hints=payload.hints)
    if not result.ok:
        raise _generation_error(result)

    validation = result.validation
    return {
        "sql": result.sql,
        "attempts": [a.as_dict() for a in result.attempts],
        "referenced_tables": validation.referenced_tables if validation else [],
        "estimated_scanned_rows": (validation.estimated_scanned_rows
                                   if validation else None),
        "executed": False,
        "provenance": PROVENANCE_NOTE,
    }


@router.post("/ask")
async def ask(
    payload: AskRequest,
    principal: Principal = Depends(requires(Permission.SQL_EXECUTE)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    """Generate, validate and run. Returns the SQL alongside the rows, always —
    an answer whose query the caller cannot see is not auditable."""
    executor = await _executor(session, principal, payload.data_source_id)
    generator = SQLGenerator(executor.validator, max_attempts=payload.max_attempts)
    result = await generator.generate(payload.question, hints=payload.hints)
    if not result.ok:
        await AuditRepository(session).record(
            action="query.generation_failed", workspace_id=principal.workspace_id,
            user_id=principal.user_id, resource_type="data_source",
            resource_id=str(payload.data_source_id))
        raise _generation_error(result)

    try:
        query = await executor.execute(result.sql or "")
    except QueryRejected as exc:
        # Generation and execution validate independently. Reaching here means
        # the two disagree, which is a bug worth surfacing rather than retrying.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "validator_disagreement",
                    "message": "A query that passed generation was rejected at "
                               "execution. This is a defect, not a bad question.",
                    "findings": exc.result.error_summary})
    except QueryFailed as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"error": "query_failed", "message": str(exc),
                                    "retryable": exc.retryable, "sql": result.sql})

    await AuditRepository(session).record(
        action="query.ask", workspace_id=principal.workspace_id,
        user_id=principal.user_id, resource_type="data_source",
        resource_id=str(payload.data_source_id))

    return {
        "question": payload.question,
        "sql": query.sql,
        "attempts": len(result.attempts),
        "columns": list(query.frame.columns),
        "rows": query.frame.head(500).to_dict(orient="records"),
        "row_count": query.row_count,
        "truncated": query.truncated,
        "duration_ms": round(query.duration_ms, 2),
        "warnings": query.warnings,
        "provenance": PROVENANCE_NOTE,
    }
