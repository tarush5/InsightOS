"""
Model training routes (spec sections 28-31).

Training is synchronous and bounded here. A real deployment moves it to the
worker — the honest reason it is inline is that the alternative is a job-status
API nobody has asked for yet, and a request that takes twelve seconds is easier
to reason about than one that returns a token.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, requires, tenant_session
from app.core.security import Permission
from app.datasources.registry import DataSourceUnavailable, get_registry
from app.ml.automl import AutoML, NotEnoughData
from app.repositories.audit import AuditRepository
from app.repositories.datasources import DataSourceRepository

router = APIRouter()


class TrainRequest(BaseModel):
    data_source_id: uuid.UUID
    table: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=200)
    date_column: str | None = None
    exclude: list[str] = Field(default_factory=list, max_length=50)
    max_rows: int = Field(default=50_000, ge=200, le=200_000)


@router.post("/train")
async def train(
    payload: TrainRequest,
    principal: Principal = Depends(requires(Permission.MODEL_TRAIN)),
    session: AsyncSession = Depends(tenant_session),
) -> dict:
    repo = DataSourceRepository(session)
    source = await repo.get(payload.data_source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Data source not found")

    catalog = await repo.catalog_for(payload.data_source_id)
    spec = catalog.get(payload.table)
    if spec is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Table '{payload.table}' is not in the crawled catalog. "
            f"Known tables: {', '.join(sorted(catalog.tables)) or '(none)'}.")
    if payload.target not in spec.columns:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Column '{payload.target}' is not on '{payload.table}'.")

    try:
        executor = await get_registry().executor(
            workspace_id=principal.workspace_id,
            data_source_id=payload.data_source_id,
            secret_ref=source.secret_ref, catalog=catalog)
    except DataSourceUnavailable as exc:
        raise HTTPException(status.HTTP_424_FAILED_DEPENDENCY, str(exc))

    columns = ", ".join(sorted(spec.columns))
    result = await executor.execute(
        f"SELECT {columns} FROM {payload.table}")  # noqa: S608 - names from catalog

    try:
        training = AutoML().train(result.frame.head(payload.max_rows),
                                  target=payload.target,
                                  date_column=payload.date_column,
                                  exclude=payload.exclude)
    except NotEnoughData as exc:
        # Not a 500: the request was valid and the data cannot support a model.
        # The message says which condition failed.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"error": "insufficient_data",
                                    "message": str(exc)})

    await AuditRepository(session).record(
        action="model.train", workspace_id=principal.workspace_id,
        user_id=principal.user_id, resource_type="model", resource_id=training.run_id)
    return {"source_rows": result.row_count, **training.as_dict()}
