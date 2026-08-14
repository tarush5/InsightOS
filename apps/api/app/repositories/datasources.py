"""Data sources and the datasets a crawl discovers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.datasources.crawler import CrawlResult
from app.datasources.secrets import validate_ref
from app.db.models import DataSource, Dataset, DatasetColumn

# Column names that identify a row rather than describe it. Used only to
# suggest a role; the analyst can override, and nothing downstream trusts it.
_ID_SUFFIXES = ("_id", "_key", "_uuid", "_code")
_MEASURE_MARKERS = ("amount", "total", "revenue", "cost", "price", "count",
                    "qty", "quantity", "score", "hours", "rate", "value")


def infer_role(name: str, data_type: str) -> str:
    lowered = name.lower()
    typed = data_type.lower()
    if any(marker in typed for marker in ("date", "time", "timestamp")):
        return "time"
    if lowered.endswith(_ID_SUFFIXES) or lowered == "id":
        return "identifier"
    if any(marker in typed for marker in ("int", "numeric", "decimal", "float",
                                          "real", "double", "money")):
        # Numeric does not mean summable. A postcode, a year, or a rating scale
        # are all numbers nobody should aggregate, so a measure has to look like
        # one by name as well as by type -- everything else stays a dimension.
        return "measure" if any(m in lowered for m in _MEASURE_MARKERS) else "dimension"
    return "dimension"


class DataSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, workspace_id: uuid.UUID, name: str, kind: str,
                     secret_ref: str, connection_meta: dict | None = None
                     ) -> DataSource:
        source = DataSource(workspace_id=workspace_id, name=name, kind=kind,
                            secret_ref=validate_ref(secret_ref),
                            connection_meta=connection_meta or {},
                            status="pending", health="unknown")
        self.session.add(source)
        await self.session.flush()
        return source

    async def get(self, data_source_id: uuid.UUID) -> DataSource | None:
        return await self.session.scalar(
            select(DataSource).where(DataSource.id == data_source_id))

    async def list_all(self) -> list[DataSource]:
        rows = await self.session.scalars(
            select(DataSource).order_by(DataSource.created_at.desc()))
        return list(rows)

    async def mark_health(self, source: DataSource, *, healthy: bool,
                          status: str | None = None) -> DataSource:
        source.health = "healthy" if healthy else "unreachable"
        if status:
            source.status = status
        await self.session.flush()
        return source

    async def record_crawl(self, source: DataSource, crawl: CrawlResult) -> int:
        """Replace the recorded schema for a source with what the crawl found.

        Replacement rather than merge is deliberate: a table dropped upstream
        must disappear from the catalog, because a stale entry is exactly the
        kind of permissive drift that lets the validator approve a query the
        warehouse will reject.
        """
        existing = await self.session.scalars(
            select(Dataset).where(Dataset.data_source_id == source.id)
            .options(selectinload(Dataset.columns)))
        for dataset in existing:
            await self.session.delete(dataset)
        await self.session.flush()

        for table in crawl.tables:
            dataset = Dataset(
                workspace_id=source.workspace_id, data_source_id=source.id,
                name=table.name, physical_table=table.name,
                row_count=table.approx_rows,
                profile={"schema": table.schema,
                         "pii_columns": table.pii_columns,
                         "column_count": len(table.columns)},
                freshness_at=datetime.now(timezone.utc))
            self.session.add(dataset)
            await self.session.flush()
            for column in table.columns:
                self.session.add(DatasetColumn(
                    dataset_id=dataset.id, name=column.name,
                    data_type=column.data_type,
                    inferred_role=infer_role(column.name, column.data_type),
                    contains_pii=column.contains_pii,
                    stats={"nullable": column.nullable,
                           "primary_key": column.is_primary_key,
                           "pii_category": column.pii_category,
                           "pii_basis": column.pii_basis}))

        source.last_sync_at = datetime.now(timezone.utc)
        source.status = "ready"
        await self.session.flush()
        return len(crawl.tables)

    async def datasets(self, data_source_id: uuid.UUID) -> list[Dataset]:
        rows = await self.session.scalars(
            select(Dataset).where(Dataset.data_source_id == data_source_id)
            .options(selectinload(Dataset.columns)).order_by(Dataset.name))
        return list(rows)

    async def catalog_for(self, data_source_id: uuid.UUID):
        """Build the validator's allowlist from the recorded schema.

        The catalog is derived from what was crawled, never hand-written, so it
        cannot claim a table the source does not have.
        """
        from app.sql_engine.validator import Catalog, TableSpec

        datasets = await self.datasets(data_source_id)
        return Catalog.from_specs([
            TableSpec(d.physical_table, {c.name for c in d.columns},
                      approx_rows=d.row_count)
            for d in datasets])
