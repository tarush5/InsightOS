"""AI safety tests for the SQL boundary (spec sections 15, 41, 62).

These are the tests that matter most: they assert the system fails *closed* when
a model produces something dangerous, rather than asserting it succeeds when it
produces something correct.
"""
import pytest

from app.sql_engine.validator import Rule

INJECTION_PAYLOADS = [
    "SELECT * FROM orders; DROP TABLE customers;",
    "SELECT * FROM orders WHERE region = 'x'; DELETE FROM orders; --",
    "SELECT * FROM orders UNION SELECT * FROM pg_catalog.pg_shadow",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM orders WHERE 1=1; UPDATE orders SET total_amount = 0;",
]

WRITE_STATEMENTS = [
    "DELETE FROM orders",
    "UPDATE orders SET total_amount = 0",
    "INSERT INTO orders VALUES (1)",
    "DROP TABLE orders",
    "TRUNCATE orders",
    "ALTER TABLE orders ADD COLUMN x INT",
    "CREATE TABLE evil (id INT)",
    "GRANT ALL ON orders TO PUBLIC",
]


@pytest.mark.parametrize("sql", INJECTION_PAYLOADS)
def test_injection_payloads_are_rejected(validator, sql):
    result = validator.validate(sql)
    assert not result.ok, f"Injection payload was allowed: {sql}"
    assert result.safe_sql is None


@pytest.mark.parametrize("sql", WRITE_STATEMENTS)
def test_write_statements_are_rejected(validator, sql):
    result = validator.validate(sql)
    assert not result.ok
    assert any(f.rule is Rule.NON_READ_STATEMENT for f in result.errors)


def test_system_catalog_access_blocked(validator):
    for sql in ("SELECT * FROM information_schema.tables",
                "SELECT * FROM pg_catalog.pg_user"):
        result = validator.validate(sql)
        assert not result.ok
        assert any(f.rule is Rule.BANNED_SCHEMA for f in result.errors)


def test_hallucinated_table_fails_closed(validator):
    """A model inventing a table must be stopped here, not at the database."""
    result = validator.validate("SELECT * FROM revenue_summary_final_v2")
    assert not result.ok
    assert any(f.rule is Rule.UNKNOWN_TABLE for f in result.errors)


def test_hallucinated_column_fails_closed(validator):
    result = validator.validate("SELECT o.profit_margin FROM orders o")
    assert not result.ok
    assert any(f.rule is Rule.UNKNOWN_COLUMN for f in result.errors)


def test_unknown_table_suggests_nearest_match(validator):
    result = validator.validate("SELECT * FROM ordrs")
    assert "orders" in result.errors[0].hint


def test_cartesian_join_rejected(validator):
    result = validator.validate("SELECT o.order_id FROM orders o JOIN customers c")
    assert not result.ok
    assert any(f.rule is Rule.CARTESIAN_JOIN for f in result.errors)


def test_ungrouped_aggregate_rejected(validator):
    result = validator.validate("SELECT region, SUM(total_amount) FROM orders")
    assert not result.ok
    assert any(f.rule is Rule.UNGROUPED_SELECT for f in result.errors)


def test_limit_is_injected_not_merely_checked(validator):
    result = validator.validate("SELECT order_id FROM orders")
    assert result.ok
    assert "LIMIT" in result.safe_sql.upper()


def test_oversized_limit_is_tightened(validator):
    v = validator
    v.max_rows = 1000
    result = v.validate("SELECT order_id FROM orders LIMIT 10000000")
    assert result.ok
    assert "1000" in result.safe_sql


def test_excessive_joins_rejected(catalog):
    from app.sql_engine.validator import SQLValidator
    v = SQLValidator(catalog, max_joins=1)
    sql = ("SELECT o.order_id FROM orders o "
           "JOIN customers c ON c.customer_id = o.customer_id "
           "JOIN customers c2 ON c2.customer_id = o.customer_id")
    assert not v.validate(sql).ok


def test_valid_analytical_query_passes(validator):
    result = validator.validate(
        "SELECT region, SUM(total_amount) AS revenue FROM orders "
        "WHERE status = 'completed' GROUP BY region"
    )
    assert result.ok, result.error_summary
    assert result.referenced_tables == ["orders"]


def test_cte_is_not_flagged_as_unknown_table(validator):
    result = validator.validate(
        "WITH monthly AS (SELECT region, SUM(total_amount) AS r FROM orders GROUP BY region) "
        "SELECT region FROM monthly"
    )
    assert result.ok, result.error_summary
