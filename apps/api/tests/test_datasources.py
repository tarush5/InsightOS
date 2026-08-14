"""Data source path: secrets, crawling, PII classification, execution safety."""
import sqlite3
from pathlib import Path

import pytest

from app.datasources.crawler import SchemaCrawler, classify_column, is_text_type
from app.datasources.secrets import (EnvSecretResolver, InvalidSecretRef, SecretNotFound,
                                     StaticSecretResolver, redact, validate_ref)
from app.repositories.datasources import infer_role
from app.sql_engine.executor import QueryRejected, SQLExecutor
from app.sql_engine.validator import SQLValidator


@pytest.fixture
def warehouse(tmp_path: Path) -> str:
    path = tmp_path / "warehouse.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE orders (order_id INTEGER PRIMARY KEY, order_date TEXT,
                             region TEXT, total_amount REAL, status TEXT);
        CREATE TABLE people (person_id INTEGER PRIMARY KEY, email TEXT,
                             notes TEXT, postcode INTEGER);
    """)
    for i in range(60):
        con.execute("INSERT INTO orders VALUES (?,?,?,?,?)",
                    (i, f"2025-08-{i % 28 + 1:02d}", "North" if i % 2 else "South",
                     100.0 + i, "completed"))
    for i in range(20):
        con.execute("INSERT INTO people VALUES (?,?,?,?)",
                    (i, f"user{i}@example.com", f"call back on user{i}@example.com",
                     10000 + i))
    con.commit()
    con.close()
    return f"sqlite+aiosqlite:///{path}"


# --- secrets -----------------------------------------------------------------

def test_a_dsn_is_rejected_as_a_reference():
    """The most likely mistake is pasting the connection string in directly."""
    with pytest.raises(InvalidSecretRef, match="connection string"):
        validate_ref("postgresql://user:pw@host:5432/db")


def test_reference_shape_is_constrained():
    assert validate_ref("payments-replica") == "payments-replica"
    with pytest.raises(InvalidSecretRef):
        validate_ref("has spaces and ; punctuation")


def test_env_resolver_maps_reference_to_variable(monkeypatch):
    monkeypatch.setenv("INSIGHTOS_SECRET_PAYMENTS_REPLICA", "sqlite+aiosqlite:///x.db")
    resolver = EnvSecretResolver()
    assert resolver.exists("payments-replica")
    assert resolver.resolve("payments-replica").endswith("x.db")


def test_missing_secret_names_the_variable_to_set():
    with pytest.raises(SecretNotFound, match="INSIGHTOS_SECRET_NOPE"):
        EnvSecretResolver().resolve("nope")


def test_redaction_masks_the_password():
    masked = redact("postgresql://insightos:hunter2@db.internal:5432/warehouse")
    assert "hunter2" not in masked
    assert "insightos" in masked and "db.internal" in masked


def test_static_resolver_round_trips():
    resolver = StaticSecretResolver()
    resolver.put("demo", "sqlite+aiosqlite:///demo.db")
    assert resolver.resolve("demo").endswith("demo.db")


# --- classification ----------------------------------------------------------

@pytest.mark.parametrize("name,samples,dtype,expected", [
    ("email", [], "VARCHAR", "email"),
    ("full_name", [], "VARCHAR", "name"),
    ("password_hash", [], "VARCHAR", "credential"),
    # The bug this guards: a date is digits and dashes, and a loose phone
    # pattern flagged every date column in the warehouse.
    ("order_date", ["2025-08-01"] * 8, "DATE", None),
    ("order_date", ["2025-08-01", "2025-08-02"] * 4, "TEXT", None),
    ("total_amount", ["126011.69", "900.5", "445.32", "12.0", "88.1", "4001.9"],
     "REAL", None),
    ("customer_id", ["1001", "1002", "1003", "1004", "1005", "1006"], "INTEGER", None),
    ("contact", ["a@b.com", "c@d.org", "e@f.net", "g@h.io", "i@j.co", "k@l.com"],
     "VARCHAR", "email"),
    ("mobile", ["+1 415 555 0132", "+44 20 7946 0958", "415-555-0199",
                "+1 (212) 555-0143", "212 555 0177", "+61 2 5550 1234"],
     "VARCHAR", "phone"),
    ("ref_code", ["A1", "B2", "C3", "D4", "E5", "F6"], "VARCHAR", None),
])
def test_pii_classification(name, samples, dtype, expected):
    assert classify_column(name, samples, dtype)[0] == expected


def test_email_in_free_text_is_found():
    """Names lie. A notes column holding addresses is the case that matters."""
    samples = [f"call back on user{i}@example.com" for i in range(6)]
    category, basis = classify_column("notes", samples, "TEXT")
    assert category == "email" and basis == "sampled_values"


def test_name_evidence_beats_an_empty_sample():
    assert classify_column("email", [], "VARCHAR") == ("email", "name")


def test_only_text_types_are_value_sampled():
    assert is_text_type("VARCHAR(200)") and not is_text_type("NUMERIC(10,2)")


@pytest.mark.parametrize("name,dtype,role", [
    ("order_date", "DATE", "time"),
    ("customer_id", "INTEGER", "identifier"),
    ("total_amount", "REAL", "measure"),
    ("region", "VARCHAR", "dimension"),
    # Numeric does not mean summable.
    ("postcode", "INTEGER", "dimension"),
])
def test_role_inference(name, dtype, role):
    assert infer_role(name, dtype) == role


# --- crawling ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_crawl_discovers_tables_columns_and_rows(warehouse):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(warehouse)
    crawl = await SchemaCrawler(engine).crawl()
    await engine.dispose()

    tables = {t.name: t for t in crawl.tables}
    assert set(tables) == {"orders", "people"}
    assert tables["orders"].approx_rows == 60
    assert {c.name for c in tables["orders"].columns} >= {"order_date", "total_amount"}


@pytest.mark.asyncio
async def test_crawl_flags_pii_and_says_the_flags_are_heuristic(warehouse):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(warehouse)
    crawl = await SchemaCrawler(engine).crawl()
    await engine.dispose()

    people = next(t for t in crawl.tables if t.name == "people")
    assert "email" in people.pii_columns
    # A postcode is quasi-identifying address data, so flagging it is correct
    # even though it is a bare integer and never a "measure".
    assert "postcode" in people.pii_columns
    orders = next(t for t in crawl.tables if t.name == "orders")
    assert orders.pii_columns == []
    assert any("heuristic" in w for w in crawl.warnings)


@pytest.mark.asyncio
async def test_catalog_is_derived_from_the_crawl(warehouse):
    """A hand-written allowlist drifts stale-permissive; this one cannot claim a
    table the source does not have."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(warehouse)
    catalog = (await SchemaCrawler(engine).crawl()).to_catalog()
    await engine.dispose()
    assert set(catalog.tables) == {"orders", "people"}


# --- execution ---------------------------------------------------------------

async def _executor(warehouse):
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(warehouse)
    catalog = (await SchemaCrawler(engine).crawl()).to_catalog()
    await engine.dispose()
    return SQLExecutor(warehouse, validator=SQLValidator(catalog=catalog))


@pytest.mark.asyncio
async def test_a_valid_query_returns_rows(warehouse):
    executor = await _executor(warehouse)
    result = await executor.execute(
        "SELECT region, SUM(total_amount) AS revenue FROM orders GROUP BY region")
    await executor.dispose()
    assert result.row_count == 2
    assert set(result.frame.columns) == {"region", "revenue"}


@pytest.mark.asyncio
@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "UPDATE orders SET total_amount = 0",
    "SELECT * FROM orders; DROP TABLE orders",
    "SELECT * FROM sqlite_master",
    "SELECT nonexistent FROM orders",
    "SELECT * FROM not_a_table",
])
async def test_dangerous_sql_never_reaches_the_database(warehouse, sql):
    executor = await _executor(warehouse)
    with pytest.raises(QueryRejected):
        await executor.execute(sql)
    await executor.dispose()


@pytest.mark.asyncio
async def test_writes_are_blocked_at_the_connection_too(warehouse):
    """Defence in depth: the validator is a parser, and parsers have bugs. A
    write that slipped past it must still fail at the database."""
    from app.sql_engine.executor import QueryFailed

    executor = await _executor(warehouse)
    executor.validator.validate = lambda sql: type(  # noqa: ARG005
        "R", (), {"ok": True, "safe_sql": sql, "referenced_tables": ["orders"],
                  "error_summary": ""})()
    with pytest.raises(QueryFailed):
        await executor.execute("DELETE FROM orders")
    await executor.dispose()


@pytest.mark.asyncio
async def test_the_row_cap_truncates_and_says_so(warehouse):
    executor = await _executor(warehouse)
    executor.max_rows = 10
    result = await executor.execute("SELECT order_id FROM orders")
    await executor.dispose()
    assert result.row_count == 10 and result.truncated
    assert any("truncated" in w.lower() for w in result.warnings)


@pytest.mark.asyncio
async def test_sqlite_reports_its_missing_timeout(warehouse):
    """A backend with weaker enforcement must be visible, not assumed safe."""
    executor = await _executor(warehouse)
    result = await executor.execute("SELECT COUNT(*) AS n FROM orders")
    await executor.dispose()
    assert any("no statement timeout" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_repr_never_leaks_the_password(warehouse):
    executor = SQLExecutor("postgresql+asyncpg://u:hunter2@h/db",
                           validator=SQLValidator(catalog=None), engine=object.__new__(type(
                               "E", (), {"dialect": type("D", (), {"name": "postgresql"})()})))
    assert "hunter2" not in repr(executor)
