"""SQL generation from governed metric definitions, and the swap into the orchestrator."""
import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from app.datasources.crawler import SchemaCrawler
from app.datasources.provider import MetricNotQueryable, SQLDataProvider
from app.semantic.registry import default_registry
from app.sql_engine.executor import SQLExecutor
from app.sql_engine.validator import SQLValidator

CURRENT = (dt.date(2025, 8, 1), dt.date(2025, 8, 31))


@pytest.fixture
def warehouse(tmp_path: Path) -> str:
    path = tmp_path / "wh.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE orders (order_id INTEGER PRIMARY KEY, order_date TEXT,
                             region TEXT, segment TEXT, channel TEXT,
                             total_amount REAL, discount_amount REAL, status TEXT);
    """)
    for i in range(240):
        day = i % 31 + 1
        region = "South" if i % 3 == 0 else "North"
        amount = 100.0 if region == "North" else 40.0
        con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                    (i, f"2025-08-{day:02d}", region, "Enterprise", "Web",
                     amount, 5.0, "completed"))
        con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)",
                    (i + 1000, f"2025-07-{day:02d}", region, "Enterprise", "Web",
                     100.0, 5.0, "completed"))
    con.commit()
    con.close()
    return f"sqlite+aiosqlite:///{path}"


async def _provider(warehouse) -> SQLDataProvider:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(warehouse)
    catalog = (await SchemaCrawler(engine, sample_values=False).crawl()).to_catalog()
    await engine.dispose()
    return SQLDataProvider(SQLExecutor(warehouse, validator=SQLValidator(catalog=catalog)))


def _metric(key: str):
    return default_registry().get(key)


# --- generation --------------------------------------------------------------

def test_every_seeded_metric_generates_sql():
    provider = SQLDataProvider.__new__(SQLDataProvider)
    provider.dialect = "sqlite"
    for metric in default_registry().all():
        sql = provider.build_sql(metric, *CURRENT, dimensions=["region"])
        assert sql.lower().startswith("select")
        assert metric.key in sql


def test_aggregate_expressions_pass_through_unwrapped():
    """The expression already says SUM(...); wrapping it again would give
    SUM(SUM(...)), which most databases reject and some silently mis-evaluate."""
    provider = SQLDataProvider.__new__(SQLDataProvider)
    provider.dialect = "sqlite"
    sql = provider.build_sql(_metric("revenue"), *CURRENT)
    assert "SUM(SUM(" not in sql.upper().replace(" ", "")


def test_metric_filters_are_applied():
    provider = SQLDataProvider.__new__(SQLDataProvider)
    provider.dialect = "sqlite"
    sql = provider.build_sql(_metric("revenue"), *CURRENT)
    assert "status" in sql and "completed" in sql


def test_a_string_date_bound_is_refused():
    """Bounds are rendered as literals, so the type check is the control that
    makes that safe. It must reject rather than coerce."""
    provider = SQLDataProvider.__new__(SQLDataProvider)
    provider.dialect = "sqlite"
    with pytest.raises(MetricNotQueryable, match="must be a date"):
        provider.build_sql(_metric("revenue"), "2025-08-01'; DROP TABLE orders--",
                           dt.date(2025, 8, 31))


def test_a_ratio_metric_without_a_ratio_expression_is_refused():
    from dataclasses import replace

    provider = SQLDataProvider.__new__(SQLDataProvider)
    provider.dialect = "sqlite"
    broken = replace(_metric("average_order_value"), expression="total_amount")
    with pytest.raises(MetricNotQueryable, match="numerator and denominator"):
        provider.build_sql(broken, *CURRENT)


def test_a_metric_with_no_expression_is_refused():
    from dataclasses import replace

    provider = SQLDataProvider.__new__(SQLDataProvider)
    provider.dialect = "sqlite"
    with pytest.raises(MetricNotQueryable, match="no expression"):
        provider.build_sql(replace(_metric("revenue"), expression=""), *CURRENT)


def test_an_invalid_filter_fragment_is_refused():
    from dataclasses import replace

    provider = SQLDataProvider.__new__(SQLDataProvider)
    provider.dialect = "sqlite"
    broken = replace(_metric("revenue"), filters=["SELECT * FROM secrets"])
    with pytest.raises(MetricNotQueryable, match="statement rather than a condition"):
        provider.build_sql(broken, *CURRENT)


# --- execution ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_generated_sql_survives_its_own_validator(warehouse):
    """Generation and validation are separate components. If the generator can
    emit something the validator rejects, one of them is wrong."""
    provider = await _provider(warehouse)
    result = await provider.fetch(_metric("revenue"), *CURRENT, dimensions=["region"])
    await provider.executor.dispose()
    assert result.row_count > 0
    assert result.validation is not None and result.validation.ok


@pytest.mark.asyncio
async def test_figures_match_a_hand_written_query(warehouse):
    provider = await _provider(warehouse)
    result = await provider.fetch(_metric("revenue"), *CURRENT, dimensions=["region"])
    direct = await provider.executor.execute(
        "SELECT SUM(total_amount) AS total FROM orders "
        "WHERE status = 'completed' AND order_date >= '2025-08-01' "
        "AND order_date <= '2025-08-31'")
    await provider.executor.dispose()
    assert result.frame["revenue"].sum() == pytest.approx(
        float(direct.frame["total"].iloc[0]))


@pytest.mark.asyncio
async def test_the_orchestrator_runs_against_a_real_database(warehouse):
    """The whole point of the boundary: nothing downstream changes."""
    from app.agents.orchestrator import (InvestigationOrchestrator,
                                         InvestigationRequest, Stage, StageState)

    provider = await _provider(warehouse)
    orchestrator = InvestigationOrchestrator(
        registry=default_registry(), data_provider=provider.as_data_provider())
    request = InvestigationRequest(
        question="Why did revenue fall in August?", metric_key="revenue",
        current_period=CURRENT,
        comparison_period=(dt.date(2025, 7, 1), dt.date(2025, 7, 31)),
        dimensions=["region", "segment", "channel"])
    events = [e async for e in orchestrator.run(request)]
    await provider.executor.dispose()

    final = events[-1]
    assert final.stage is Stage.COMPLETE and final.state is StageState.DONE
    assert final.detail["drivers"][0]["segment"] == "South"
    assert final.detail["critic"]["approved"]


@pytest.mark.asyncio
async def test_a_sync_provider_still_works(warehouse):
    """The demo CSV provider is synchronous; supporting both keeps the swap a
    one-line change."""
    import pandas as pd

    from app.agents.orchestrator import (InvestigationOrchestrator,
                                         InvestigationRequest, Stage, StageState)

    frame = pd.DataFrame({
        "order_date": pd.date_range("2025-07-01", periods=62, freq="D"),
        "region": ["North", "South"] * 31,
        "segment": ["Enterprise"] * 62,
        "channel": ["Web"] * 62,
        "revenue": [1000.0] * 31 + [600.0] * 31,
    })

    def sync_provider(metric, start, end):
        mask = ((frame.order_date >= pd.Timestamp(start)) &
                (frame.order_date <= pd.Timestamp(end)))
        return frame.loc[mask].copy()

    orchestrator = InvestigationOrchestrator(registry=default_registry(),
                                             data_provider=sync_provider)
    events = [e async for e in orchestrator.run(InvestigationRequest(
        question="What changed?", metric_key="revenue",
        current_period=(dt.date(2025, 8, 1), dt.date(2025, 8, 31)),
        comparison_period=(dt.date(2025, 7, 1), dt.date(2025, 7, 31)),
        dimensions=["region"]))]
    assert events[-1].stage is Stage.COMPLETE
    assert events[-1].state is StageState.DONE


@pytest.mark.asyncio
async def test_a_dimension_the_source_lacks_is_dropped_and_named(warehouse):
    """A metric definition can outlive the schema it describes. Dropping the
    stale breakdown keeps the investigation running; naming it keeps the
    omission from being silent."""
    provider = await _provider(warehouse)
    result = await provider.fetch(_metric("revenue"), *CURRENT,
                                  dimensions=["region", "category"])
    await provider.executor.dispose()
    assert "region" in result.frame.columns
    assert "category" not in result.frame.columns
    assert any("category" in w for w in result.warnings)


def test_a_self_alias_cannot_smuggle_an_unknown_column():
    """`SELECT category AS category` defines an alias named after the column it
    selects. Exempting it from the catalog check let any unknown column through
    -- which is what a generated projection emits."""
    from app.sql_engine.validator import Catalog, SQLValidator, TableSpec

    catalog = Catalog.from_specs([
        TableSpec("orders", {"order_id", "order_date", "region"}, approx_rows=100)])
    validator = SQLValidator(catalog=catalog)
    assert not validator.validate("SELECT category AS category FROM orders").ok
    assert validator.validate("SELECT region AS region FROM orders").ok
    assert validator.validate(
        "SELECT region, COUNT(*) AS n FROM orders GROUP BY region ORDER BY n").ok
