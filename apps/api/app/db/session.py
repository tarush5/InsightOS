"""
Async session factory with structural tenant isolation.

The isolation is not "remember to add a WHERE clause". A session opened for a
workspace registers a ``with_loader_criteria`` for every ``WorkspaceScoped``
model, so SQLAlchemy appends the tenant predicate to every SELECT touching
those tables -- including relationship loads and queries written without a
filter. Forgetting the clause in application code cannot leak another tenant's
rows; it can only return nothing.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, orm
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models import Base, WorkspaceScoped

_ASYNC_DRIVERS = {"postgresql": "postgresql+asyncpg", "sqlite": "sqlite+aiosqlite"}


def async_url(url: str | None = None) -> str:
    """Normalise a sync DSN to its async driver so one env var serves both."""
    url = url or settings.DATABASE_URL
    if "+" in url.split("://", 1)[0]:
        scheme = url.split("://", 1)[0]
        if "asyncpg" in scheme or "aiosqlite" in scheme:
            return url
        url = url.split("+", 1)[0] + "://" + url.split("://", 1)[1]
    scheme = url.split("://", 1)[0]
    return url.replace(scheme + "://", _ASYNC_DRIVERS.get(scheme, scheme) + "://", 1)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(
            async_url(), pool_pre_ping=True, echo=False,
            future=True,
        )
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def _tenant_models() -> list[type]:
    return [m.class_ for m in Base.registry.mappers
            if issubclass(m.class_, WorkspaceScoped)]


def apply_tenant_scope(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Attach the tenant predicate to every future SELECT on this session."""
    models = _tenant_models()

    @event.listens_for(session.sync_session, "do_orm_execute")
    def _scope(execute_state):
        if not execute_state.is_select or execute_state.is_column_load:
            return
        for model in models:
            execute_state.statement = execute_state.statement.options(
                orm.with_loader_criteria(
                    model,
                    lambda cls: cls.workspace_id == workspace_id,
                    include_aliases=True,
                )
            )

    session.info["workspace_id"] = workspace_id


@asynccontextmanager
async def session_scope(workspace_id: uuid.UUID | None = None) -> AsyncIterator[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        if workspace_id is not None:
            apply_tenant_scope(session, workspace_id)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_all() -> None:
    """Schema bootstrap for tests and local runs. Production uses Alembic."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
