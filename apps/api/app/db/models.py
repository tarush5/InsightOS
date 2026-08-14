"""
SQLAlchemy 2.0 models (spec section 56).

Tenant isolation is structural: every workspace-owned table carries a
``workspace_id`` FK and the session layer applies a workspace filter, so a
missing WHERE clause in application code cannot leak across tenants.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer,
                        Numeric, String, Text, UniqueConstraint, func)
from app.db.types import GUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(GUID(), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkspaceScoped:
    """Mixin marking a table as tenant-owned. `require_workspace` in deps.py
    asserts every query against these models carries a workspace filter."""
    @classmethod
    def __declare_last__(cls) -> None:
        assert hasattr(cls, "workspace_id"), f"{cls.__name__} must define workspace_id"


# --- Identity ----------------------------------------------------------------

class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(32), default="standard")
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    memberships: Mapped[list["Membership"]] = relationship(back_populates="workspace")


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Membership(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id", name="uq_member"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")

    user: Mapped[User] = relationship(back_populates="memberships")
    workspace: Mapped[Workspace] = relationship(back_populates="memberships")


class RefreshToken(Base, TimestampMixin):
    """Persisted so refresh tokens can be revoked; the jti is also cached in Redis."""
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- Data plane --------------------------------------------------------------

class DataSource(Base, TimestampMixin, WorkspaceScoped):
    __tablename__ = "data_sources"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(32))          # postgres|mysql|sqlite|csv|...
    # Credentials are stored as a reference into the secret manager, never inline.
    secret_ref: Mapped[str] = mapped_column(String(255), default="")
    connection_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health: Mapped[str] = mapped_column(String(24), default="unknown")
    quality_score: Mapped[float | None] = mapped_column(Numeric(5, 2))


class Dataset(Base, TimestampMixin, WorkspaceScoped):
    __tablename__ = "datasets"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    physical_table: Mapped[str] = mapped_column(String(200))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    freshness_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    columns: Mapped[list["DatasetColumn"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan")


class DatasetColumn(Base):
    __tablename__ = "dataset_columns"
    id: Mapped[uuid.UUID] = _pk()
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    data_type: Mapped[str] = mapped_column(String(64))
    inferred_role: Mapped[str] = mapped_column(String(32), default="dimension")
    null_pct: Mapped[float] = mapped_column(Numeric(6, 5), default=0)
    unique_count: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    contains_pii: Mapped[bool] = mapped_column(Boolean, default=False)

    dataset: Mapped[Dataset] = relationship(back_populates="columns")


class Metric(Base, TimestampMixin, WorkspaceScoped):
    """Governed semantic-layer metric. Versioned; only APPROVED rows are usable."""
    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("workspace_id", "key", "version", name="uq_metric_ver"),)
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(96), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    aggregation: Mapped[str] = mapped_column(String(32))
    expression: Mapped[str] = mapped_column(Text, nullable=False)
    base_table: Mapped[str] = mapped_column(String(200))
    date_column: Mapped[str] = mapped_column(String(200))
    dimensions: Mapped[list] = mapped_column(JSON, default=list)
    filters: Mapped[list] = mapped_column(JSON, default=list)
    unit: Mapped[str] = mapped_column(String(32), default="count")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    owner_email: Mapped[str] = mapped_column(String(320), default="")
    approved_by: Mapped[str | None] = mapped_column(String(320))
    approved_on: Mapped[date | None] = mapped_column(Date)


# --- Investigations ----------------------------------------------------------

class Investigation(Base, TimestampMixin, WorkspaceScoped):
    __tablename__ = "investigations"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reference: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    metric_key: Mapped[str | None] = mapped_column(String(96))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    verdict: Mapped[str | None] = mapped_column(String(32))
    headline: Mapped[str | None] = mapped_column(Text)
    narrative: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    recommendations: Mapped[list] = mapped_column(JSON, default=list)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("investigations.id"))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))

    runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan")


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = _pk()
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(48))
    state: Mapped[str] = mapped_column(String(24))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[float | None] = mapped_column(Numeric(12, 2))

    investigation: Mapped["Investigation"] = relationship(back_populates="runs")


class ToolCall(Base, TimestampMixin):
    """Full trace of every tool an agent invoked -- the audit spine for section 30."""
    __tablename__ = "tool_calls"
    id: Mapped[uuid.UUID] = _pk()
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    tool_name: Mapped[str] = mapped_column(String(96))
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    result_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="ok")
    latency_ms: Mapped[float | None] = mapped_column(Numeric(12, 2))


# --- ML ----------------------------------------------------------------------

class MLModel(Base, TimestampMixin, WorkspaceScoped):
    __tablename__ = "ml_models"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    task_type: Mapped[str] = mapped_column(String(48))
    algorithm: Mapped[str] = mapped_column(String(96))
    version: Mapped[int] = mapped_column(Integer, default=1)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    hyperparameters: Mapped[dict] = mapped_column(JSON, default=dict)
    feature_names: Mapped[list] = mapped_column(JSON, default=list)
    artifact_uri: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(24), default="training")


class Alert(Base, TimestampMixin, WorkspaceScoped):
    __tablename__ = "alerts"
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    natural_language: Mapped[str] = mapped_column(Text)
    rule: Mapped[dict] = mapped_column(JSON, default=dict)   # compiled structured rule
    metric_key: Mapped[str] = mapped_column(String(96))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    """Append-only. Never updated, never deleted (spec section 42)."""
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_ws_time", "workspace_id", "occurred_at"),)
    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(GUID())
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str] = mapped_column(String(96), default="")
    status: Mapped[str] = mapped_column(String(24), default="success")
    ip_address: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
