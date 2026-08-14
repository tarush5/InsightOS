"""
Data source routes (spec sections 12-15).

Registering a source is separate from crawling it, and both are separate from
querying it. That sequencing is deliberate: a connection that cannot be reached
should fail at registration with a message about the credential, not three
screens later inside an investigation that blames the metric.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.deps import Principal, current_principal, requires, tenant_session
from app.core.security import Permission
from app.datasources.crawler import SchemaCrawler
from app.datasources.registry import DataSourceUnavailable, get_registry
from app.datasources.secrets import (InvalidSecretRef, SecretNotFound,
                                     default_resolver, redact, validate_ref)
from app.repositories.audit import AuditRepository
from app.repositories.datasources import DataSourceRepository

router = APIRouter()

SUPPORTED_KINDS = {"postgres", "sqlite"}


class CreateDataSource(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    kind: str = Field(default="postgres")
    secret_ref: str = Field(min_length=1, max_length=200)
    connection_meta: dict = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _supported(cls, v: str) -> str:
        if v not in SUPPORTED_KINDS:
            raise ValueError(
                f"'{v}' is not supported yet. Supported: "
                f"{', '.join(sorted(SUPPORTED_KINDS))}.")
        return v



def _serialise(source) -> dict:
    return {"id": str(source.id), "name": source.name, "kind": source.kind,
            "secret_ref": source.secret_ref, "status": source.status,
            "health": source.health,
            "last_sync_at": (source.last_sync_at.isoformat()
                             if source.last_sync_at else None)}


async def _load(session: AsyncSession, data_source_id: uuid.UUID):
    source = await DataSourceRepository(session).get(data_source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found")
    return source


def _resolve_dsn(secret_ref: str) -> str:
    try:
        return default_resolver().resolve(secret_ref)
    except SecretNotFound as exc:
        raise HTTPException(status.HTTP_424_FAILED_DEPENDENCY, str(exc))


@router.post("", status_code=201)
async def create_data_source(
    payload: CreateDataSource,
    principal: Principal = Depends(requires(Permission.DATASOURCE_WRITE)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    # Deliberately not a Pydantic field validator. FastAPI's 422 body echoes the
    # offending input, so rejecting a pasted DSN there would put the password
    # into the error response and from there into logs and error trackers --
    # while telling the user off for pasting it. Validating here lets the
    # response name the problem without repeating the value.
    try:
        validate_ref(payload.secret_ref)
    except InvalidSecretRef as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"error": "invalid_secret_ref",
                                    "message": str(exc)})

    dsn = _resolve_dsn(payload.secret_ref)
    repo = DataSourceRepository(session)
    source = await repo.create(workspace_id=principal.workspace_id,
                               name=payload.name, kind=payload.kind,
                               secret_ref=payload.secret_ref,
                               connection_meta=payload.connection_meta)

    engine = create_async_engine(dsn)
    try:
        from sqlalchemy import text
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await repo.mark_health(source, healthy=True, status="connected")
    except Exception:
        # Registered but unreachable, rather than refused: the reference may be
        # right and the network temporarily wrong, and losing the registration
        # would make the user re-enter everything.
        await repo.mark_health(source, healthy=False, status="unreachable")
    finally:
        await engine.dispose()

    await AuditRepository(session).record(
        action="datasource.create", workspace_id=principal.workspace_id,
        user_id=principal.user_id, resource_type="data_source",
        resource_id=str(source.id))
    return {**_serialise(source),
            "target": redact(dsn),
            "next_step": ("Run POST /api/v1/datasources/{id}/crawl to discover "
                          "its schema before querying it."
                          if source.health == "healthy" else
                          "The credential resolved but the source did not "
                          "answer. Check network reachability, then retry.")}


@router.get("")
async def list_data_sources(
    principal: Principal = Depends(requires(Permission.DATASOURCE_READ)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    sources = await DataSourceRepository(session).list_all()
    return {"data_sources": [_serialise(s) for s in sources]}


@router.post("/{data_source_id}/crawl")
async def crawl_data_source(
    data_source_id: uuid.UUID,
    sample_values: bool = True,
    principal: Principal = Depends(requires(Permission.DATASOURCE_WRITE)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    repo = DataSourceRepository(session)
    source = await _load(session, data_source_id)
    dsn = _resolve_dsn(source.secret_ref)

    engine = create_async_engine(dsn)
    try:
        crawl = await SchemaCrawler(engine, sample_values=sample_values).crawl()
    except Exception:
        await repo.mark_health(source, healthy=False, status="unreachable")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not read the schema. Check that the credential has USAGE on "
            "the schema and SELECT on its tables.")
    finally:
        await engine.dispose()

    table_count = await repo.record_crawl(source, crawl)
    await repo.mark_health(source, healthy=True, status="ready")
    # The cached executor holds the previous catalog, which no longer matches.
    get_registry().invalidate(principal.workspace_id, data_source_id)

    await AuditRepository(session).record(
        action="datasource.crawl", workspace_id=principal.workspace_id,
        user_id=principal.user_id, resource_type="data_source",
        resource_id=str(source.id))
    return {"data_source_id": str(source.id), "tables_recorded": table_count,
            **crawl.as_dict()}


@router.get("/{data_source_id}/schema")
async def get_schema(
    data_source_id: uuid.UUID,
    principal: Principal = Depends(requires(Permission.DATASOURCE_READ)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    await _load(session, data_source_id)
    datasets = await DataSourceRepository(session).datasets(data_source_id)
    return {"datasets": [
        {"name": d.name, "physical_table": d.physical_table,
         "approx_rows": d.row_count,
         "pii_columns": (d.profile or {}).get("pii_columns", []),
         "columns": [{"name": c.name, "data_type": c.data_type,
                      "role": c.inferred_role, "contains_pii": c.contains_pii,
                      "pii_category": (c.stats or {}).get("pii_category")}
                     for c in d.columns]}
        for d in datasets]}


@router.post("/{data_source_id}/test")
async def test_connection(
    data_source_id: uuid.UUID,
    principal: Principal = Depends(requires(Permission.DATASOURCE_READ)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    repo = DataSourceRepository(session)
    source = await _load(session, data_source_id)
    catalog = await repo.catalog_for(data_source_id)
    if not catalog.tables:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No schema has been recorded for this source. Crawl it first — "
            "queries are validated against the crawled catalog, so an empty "
            "catalog rejects everything.")
    try:
        executor = await get_registry().executor(
            workspace_id=principal.workspace_id, data_source_id=data_source_id,
            secret_ref=source.secret_ref, catalog=catalog)
    except DataSourceUnavailable as exc:
        raise HTTPException(status.HTTP_424_FAILED_DEPENDENCY, str(exc))

    healthy = await executor.healthcheck()
    await repo.mark_health(source, healthy=healthy)
    return {"data_source_id": str(source.id), "healthy": healthy,
            "dialect": executor.dialect,
            "tables_in_catalog": len(catalog.tables)}
