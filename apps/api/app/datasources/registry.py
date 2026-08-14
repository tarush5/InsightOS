"""
Per-workspace data source connections.

Engines are expensive to build and cheap to keep, so one executor is cached per
data source and reused. The cache key includes the workspace id even though the
data source id is already unique: a cache keyed on something a caller can
influence is how one tenant ends up holding another's connection, and the extra
component costs nothing.

Connections are opened lazily. A workspace with five configured sources does not
open five pools at startup, and a source that is unreachable fails when it is
used rather than preventing the API from starting.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from app.datasources.secrets import SecretNotFound, SecretResolver, default_resolver
from app.sql_engine.executor import SQLExecutor
from app.sql_engine.validator import Catalog, SQLValidator

log = logging.getLogger(__name__)


class DataSourceUnavailable(RuntimeError):
    """The source exists but cannot currently be used. Message is user-facing."""


@dataclass(frozen=True, slots=True)
class _Key:
    workspace_id: uuid.UUID
    data_source_id: uuid.UUID


class DataSourceRegistry:
    """Owns executor lifetime. One instance per process."""

    def __init__(self, resolver: SecretResolver | None = None) -> None:
        self._resolver = resolver or default_resolver()
        self._executors: dict[_Key, SQLExecutor] = {}

    async def executor(self, *, workspace_id: uuid.UUID, data_source_id: uuid.UUID,
                       secret_ref: str, catalog: Catalog) -> SQLExecutor:
        key = _Key(workspace_id, data_source_id)
        cached = self._executors.get(key)
        if cached is not None:
            return cached

        try:
            dsn = self._resolver.resolve(secret_ref)
        except SecretNotFound as exc:
            # The reference is safe to show; whatever it points at is not.
            raise DataSourceUnavailable(str(exc)) from None

        executor = SQLExecutor(dsn, validator=SQLValidator(catalog=catalog))
        self._executors[key] = executor
        log.info("datasource.connected workspace=%s source=%s dialect=%s",
                 workspace_id, data_source_id, executor.dialect)
        return executor

    def invalidate(self, workspace_id: uuid.UUID, data_source_id: uuid.UUID) -> None:
        """Drop a cached executor after the catalog or credential changes.

        The old engine is left to be garbage collected rather than disposed
        inline, because callers mid-query still hold it.
        """
        self._executors.pop(_Key(workspace_id, data_source_id), None)

    async def dispose_all(self) -> None:
        for executor in list(self._executors.values()):
            await executor.dispose()
        self._executors.clear()


_registry = DataSourceRegistry()


def get_registry() -> DataSourceRegistry:
    return _registry
