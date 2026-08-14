"""Text-to-SQL: the validator is the gate, and repair feeds its findings back."""
import pytest

from app.llm.gateway import LLMResponse
from app.sql_engine.generator import (SQLGenerator, build_schema_context,
                                      extract_sql)
from app.sql_engine.validator import Catalog, SQLValidator, TableSpec

CATALOG = Catalog.from_specs([
    TableSpec("orders", {"order_id", "order_date", "region", "total_amount", "status"},
              approx_rows=40_454),
    TableSpec("customers", {"customer_id", "region", "segment", "signup_date"},
              approx_rows=2_500),
])


class ScriptedGateway:
    """Returns queued replies in order, recording the prompt it was given.

    A stub rather than a live model on purpose: the behaviour under test is the
    validate-and-repair loop, and that has to be deterministic to be a
    regression test at all.
    """

    def __init__(self, replies: list[str], degraded: bool = False) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.degraded = degraded

    async def complete(self, *, task, system, prompt, **kwargs) -> LLMResponse:
        self.prompts.append(prompt)
        text = self.replies.pop(0) if self.replies else ""
        return LLMResponse(text=text, model="scripted", provider="test",
                           latency_ms=1.0, degraded=self.degraded)


def _generator(replies, **kwargs):
    gateway = ScriptedGateway(replies, degraded=kwargs.pop("degraded", False))
    return SQLGenerator(SQLValidator(catalog=CATALOG), gateway=gateway, **kwargs), gateway


# --- schema context ----------------------------------------------------------

def test_schema_context_lists_tables_and_columns():
    context = build_schema_context(CATALOG)
    assert "orders" in context and "total_amount" in context
    assert "40,454" in context


def test_truncation_is_disclosed_not_hidden():
    """A model shown a silently trimmed list will confidently use the wrong
    table; one told it is seeing part of the schema can say so."""
    big = Catalog.from_specs([
        TableSpec(f"t{i}", {"a", "b"}, approx_rows=1) for i in range(60)])
    context = build_schema_context(big, max_tables=10)
    assert "further tables not shown" in context


def test_empty_catalog_says_so():
    assert "no tables available" in build_schema_context(None)


@pytest.mark.parametrize("raw,expected", [
    ("```sql\nSELECT 1 FROM orders\n```", "SELECT 1 FROM orders"),
    ("Here you go:\n\nSELECT region FROM orders;", "SELECT region FROM orders"),
    ("SELECT a FROM t", "SELECT a FROM t"),
    ("```\nWITH x AS (SELECT 1) SELECT * FROM x\n```",
     "WITH x AS (SELECT 1) SELECT * FROM x"),
])
def test_sql_extraction(raw, expected):
    assert extract_sql(raw) == expected


# --- the happy path ----------------------------------------------------------

@pytest.mark.asyncio
async def test_a_valid_query_is_accepted_on_the_first_attempt():
    generator, _ = _generator([
        "SELECT region, SUM(total_amount) AS revenue FROM orders GROUP BY region"])
    result = await generator.generate("Revenue by region?")
    assert result.ok and result.attempt_count == 1
    assert "LIMIT" in (result.sql or "").upper()   # injected by the validator


@pytest.mark.asyncio
async def test_the_returned_sql_is_the_validated_rewrite():
    """The caller must execute what passed validation, not the raw model output,
    or the injected LIMIT is silently dropped."""
    generator, _ = _generator([
        "SELECT region, COUNT(*) AS n FROM orders GROUP BY region"])
    result = await generator.generate("Orders per region?")
    assert result.validation is not None
    assert result.sql == result.validation.safe_sql


# --- the gate ----------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("dangerous", [
    "DELETE FROM orders",
    "SELECT * FROM orders; DROP TABLE orders",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM salaries",
    "SELECT secret_column FROM orders",
])
async def test_dangerous_generated_sql_never_escapes(dangerous):
    """Everything the model proposes goes through the same validator as
    hand-written SQL. Plausibility is not authorisation."""
    generator, _ = _generator([dangerous] * 3)
    result = await generator.generate("Give me everything")
    assert not result.ok
    assert result.sql is None


# --- repair ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_rejected_query_is_repaired_using_the_validator_findings():
    generator, gateway = _generator([
        "SELECT regionn, SUM(total_amount) AS revenue FROM orders GROUP BY regionn",
        "SELECT region, SUM(total_amount) AS revenue FROM orders GROUP BY region",
    ])
    result = await generator.generate("Revenue by region?")
    assert result.ok and result.attempt_count == 2
    assert not result.attempts[0].ok and result.attempts[1].ok


@pytest.mark.asyncio
async def test_the_repair_prompt_carries_the_specific_rule_and_the_hint():
    """A model told only that something was wrong rewrites from scratch and
    reintroduces a different mistake."""
    generator, gateway = _generator([
        "SELECT regionn FROM orders",
        "SELECT region FROM orders GROUP BY region",
    ])
    await generator.generate("Regions?")
    repair = gateway.prompts[1]
    assert "unknown_column" in repair
    assert "regionn" in repair            # the rejected SQL, verbatim
    assert "Did you mean 'region'?" in repair


@pytest.mark.asyncio
async def test_attempts_are_capped_and_reported():
    generator, gateway = _generator(["SELECT nope FROM orders"] * 5, max_attempts=3)
    result = await generator.generate("Something impossible")
    assert not result.ok
    assert result.attempt_count == 3
    assert len(gateway.prompts) == 3
    assert "3 attempts" in result.reason


@pytest.mark.asyncio
async def test_every_attempt_is_recorded_for_inspection():
    generator, _ = _generator([
        "SELECT nope FROM orders",
        "SELECT region FROM orders GROUP BY region",
    ])
    result = await generator.generate("Regions?")
    assert [a.ok for a in result.attempts] == [False, True]
    assert result.attempts[0].errors


# --- honest failure ----------------------------------------------------------

@pytest.mark.asyncio
async def test_no_model_configured_declines_rather_than_guessing():
    """A fabricated query that happens to parse is worse than a refusal,
    because the first one returns numbers."""
    generator, _ = _generator(["SELECT 1"], degraded=True)
    result = await generator.generate("Revenue by region?")
    assert not result.ok and result.degraded
    assert "governed metric" in result.reason


@pytest.mark.asyncio
async def test_a_model_refusal_is_honoured_not_retried():
    """The repair prompt offers UNANSWERABLE. Treating that as another parse
    failure would spend the remaining retries pushing the model to invent a
    table it has already said does not exist."""
    generator, gateway = _generator([
        "SELECT profit_margin FROM orders",
        "UNANSWERABLE",
        "SELECT region FROM orders GROUP BY region",
    ])
    result = await generator.generate("What is our profit margin?")
    assert not result.ok
    assert result.attempt_count == 1
    assert len(gateway.prompts) == 2      # stopped; did not use the third reply
    assert "does not contain" in result.reason


@pytest.mark.asyncio
async def test_an_empty_reply_is_retried_with_a_pointed_prompt():
    generator, gateway = _generator([
        "I'm not sure how to help with that.",
        "SELECT region FROM orders GROUP BY region",
    ])
    result = await generator.generate("Regions?")
    assert result.ok
    assert "No SQL statement was found" in gateway.prompts[1]


@pytest.mark.parametrize("prose", [
    "I'm not sure how to help with that.",
    "That depends on what you mean by revenue.",
    "I could do that with a join, but the schema is unclear.",
])
def test_prose_is_not_mistaken_for_sql(prose):
    """An unanchored keyword search matches the ordinary English 'with', and
    returned 'with a join, but...' as if it were a query."""
    assert extract_sql(prose) == ""
