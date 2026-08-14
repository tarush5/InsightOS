import os
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Persistence tests run against SQLite so the suite needs no server. The models
# are dialect-portable (see app/db/types.GUID) precisely so this is possible;
# anything that only works on Postgres would silently pass here and fail in
# production, so the schema is kept free of Postgres-only constructs.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("AUTH_SECRET", "test-only-secret-not-used-anywhere-else")
# Full-strength PBKDF2 on every fixture makes the suite slow enough that people
# stop running it. Production keeps the OWASP floor; config.py refuses to accept
# a lowered value outside local.
os.environ.setdefault("PASSWORD_HASH_ROUNDS", "1000")
os.environ.setdefault("SEED_DIR", str(Path(__file__).resolve().parents[3] / "seed"))

from app.sql_engine.validator import Catalog, SQLValidator, TableSpec  # noqa: E402


@pytest.fixture
def catalog() -> Catalog:
    return Catalog.from_specs([
        TableSpec("orders", {"order_id", "customer_id", "order_date", "region",
                             "segment", "channel", "category", "status",
                             "total_amount", "discount_amount"}, 40_000),
        TableSpec("customers", {"customer_id", "company_name", "segment", "region",
                                "signup_date", "churn_date"}, 2_500),
    ])


@pytest.fixture
def validator(catalog) -> SQLValidator:
    return SQLValidator(catalog)


@pytest.fixture
def two_periods():
    """Prev period is flat; curr period has an engineered -50% hit in one segment."""
    rows_prev, rows_curr = [], []
    for day in range(30):
        for region in ("North", "South", "East"):
            rows_prev.append({"order_date": pd.Timestamp("2025-07-01") + pd.Timedelta(days=day),
                              "region": region, "segment": "Enterprise", "revenue": 1000.0})
            value = 500.0 if region == "South" else 1000.0
            rows_curr.append({"order_date": pd.Timestamp("2025-08-01") + pd.Timedelta(days=day),
                              "region": region, "segment": "Enterprise", "revenue": value})
    return pd.DataFrame(rows_prev), pd.DataFrame(rows_curr)


@pytest_asyncio.fixture
async def db():
    """A fresh in-memory schema per test.

    A file-backed URL would leak state between tests and turn ordering into a
    hidden dependency, so each test gets its own StaticPool-shared connection.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.db import session as session_module
    from app.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_module._engine = engine
    session_module._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine
    finally:
        await engine.dispose()
        session_module._engine = None
        session_module._sessionmaker = None


@pytest_asyncio.fixture
async def client(db):
    """HTTP client bound to the app in-process, sharing the test schema."""
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as http_client:
        yield http_client


@pytest_asyncio.fixture
async def registered(client):
    """A signed-up workspace admin plus an auth header ready to use."""
    email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    response = await client.post("/api/v1/auth/signup", json={
        "email": email, "password": "correct-horse-battery-staple",
        "full_name": "Test Owner", "workspace_name": "Test Workspace"})
    assert response.status_code == 201, response.text
    tokens = response.json()
    return {"email": email, "password": "correct-horse-battery-staple",
            **tokens,
            "headers": {"Authorization": f"Bearer {tokens['access_token']}"}}


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter is a process-wide singleton by design, which is correct in
    production and cross-contaminating in tests: every test shares one client
    address, so one test's signups would exhaust the next test's budget."""
    from app.core.ratelimit import get_limiter

    get_limiter()._local.clear()
    yield
    get_limiter()._local.clear()
