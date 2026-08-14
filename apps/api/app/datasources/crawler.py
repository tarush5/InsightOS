"""
Schema discovery.

Crawls a data source's tables and columns into `datasets` / `dataset_columns`,
which is what builds the `Catalog` the SQL validator checks against. Without
this the validator's allowlist has to be hand-written, and a hand-written
allowlist drifts out of date in exactly the direction that matters: it goes
stale permissive.

Two things the crawl does beyond listing names:

**Row estimates** come from the planner's statistics rather than `COUNT(*)`,
because counting every table on every crawl is how a discovery job becomes an
outage. They feed the validator's scan estimate, where being roughly right is
enough.

**PII classification** flags columns whose name or sampled values look like
personal data. It is a heuristic and is labelled as one — it produces a
suggestion for a human to confirm, never a guarantee of coverage. A classifier
presented as authoritative is worse than none, because it stops people looking.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)

SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast", "sys",
                  "sqlite_master", "sqlite_temp_master"}

# Name patterns. Ordered most to least specific; first match wins.
PII_NAME_PATTERNS: list[tuple[str, str]] = [
    (r"(^|_)(ssn|social_security|nin|national_id)($|_)", "government_id"),
    (r"(^|_)(passport|driver_licen[cs]e)($|_)", "government_id"),
    (r"(^|_)(card_number|pan|cvv|iban|account_number|routing)($|_)", "financial"),
    (r"(^|_)(password|secret|api_key|token|hash)($|_)", "credential"),
    (r"(^|_)(email|e_mail)($|_)", "email"),
    (r"(^|_)(phone|mobile|telephone|msisdn)($|_)", "phone"),
    (r"(^|_)(dob|date_of_birth|birth_date|birthday)($|_)", "date_of_birth"),
    (r"(^|_)(first_name|last_name|full_name|surname|given_name)($|_)", "name"),
    (r"(^|_)(address|street|postcode|postal_code|zip|zipcode)($|_)", "address"),
    (r"(^|_)(latitude|longitude|lat|lng|geo)($|_)", "location"),
    (r"(^|_)(ip_address|ip_addr|user_agent|device_id)($|_)", "device"),
]

# Value patterns, used on a small sample of *text* columns only. Names lie -- a
# column called `notes` holding email addresses is the case that matters.
#
# These are anchored and narrow because the first version was not: a loose phone
# pattern flagged `order_date` ("2025-08-01") and `total_amount`
# ("126011.696434"), both of which are digits and separators. A PII classifier
# that cries wolf on every numeric column gets switched off, which is a worse
# outcome than one that misses something.
PII_VALUE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"), "email"),
    (re.compile(r"^\d{3}-\d{2}-\d{4}$"), "government_id"),
    # Phone: 9-15 digits, optional country code, no more than 4 separators, and
    # never a date (which a bare digit-and-dash pattern happily matches).
    (re.compile(r"^\+?(?!\d{4}-\d{2}-\d{2}$)(?=(?:\D*\d){9,15}\D*$)"
                r"[\d][\d\s().-]{7,18}\d$"), "phone"),
    (re.compile(r"^(?:\d[ -]?){13,19}$"), "financial"),
]

# Free text rarely matches a pattern end to end, but an email address buried in
# a notes field is still personal data.
PII_SUBSTRING_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b"), "email"),
]

# Value sampling only applies to text columns. A DATE, NUMERIC or BOOLEAN column
# cannot hold a phone number in any form worth flagging, and treating it as if
# it could is where the false positives came from.
TEXT_TYPE_MARKERS = ("char", "text", "string", "clob", "varchar", "json", "uuid")

SAMPLE_ROWS = 200
VALUE_MATCH_THRESHOLD = 0.30
SUBSTRING_MATCH_THRESHOLD = 0.20


@dataclass(slots=True)
class ColumnInfo:
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    pii_category: str | None = None
    pii_basis: str | None = None       # "name" | "sampled_values"

    @property
    def contains_pii(self) -> bool:
        return self.pii_category is not None

    def as_dict(self) -> dict:
        return {"name": self.name, "data_type": self.data_type,
                "nullable": self.nullable, "is_primary_key": self.is_primary_key,
                "contains_pii": self.contains_pii,
                "pii_category": self.pii_category, "pii_basis": self.pii_basis}


@dataclass(slots=True)
class TableInfo:
    name: str
    schema: str | None
    columns: list[ColumnInfo]
    approx_rows: int = 0

    @property
    def pii_columns(self) -> list[str]:
        return [c.name for c in self.columns if c.contains_pii]

    def as_dict(self) -> dict:
        return {"name": self.name, "schema": self.schema,
                "approx_rows": self.approx_rows,
                "pii_columns": self.pii_columns,
                "columns": [c.as_dict() for c in self.columns]}


@dataclass(slots=True)
class CrawlResult:
    tables: list[TableInfo] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def pii_table_count(self) -> int:
        return sum(1 for t in self.tables if t.pii_columns)

    def as_dict(self) -> dict:
        return {"table_count": len(self.tables),
                "column_count": sum(len(t.columns) for t in self.tables),
                "tables_with_pii": self.pii_table_count,
                "skipped": self.skipped, "warnings": self.warnings,
                "tables": [t.as_dict() for t in self.tables]}

    def to_catalog(self):
        """Build the validator's allowlist from what was actually discovered."""
        from app.sql_engine.validator import Catalog, TableSpec

        return Catalog.from_specs([
            TableSpec(t.name, {c.name for c in t.columns}, approx_rows=t.approx_rows)
            for t in self.tables])


def is_text_type(data_type: str | None) -> bool:
    return bool(data_type) and any(marker in data_type.lower()
                                   for marker in TEXT_TYPE_MARKERS)


def classify_column(name: str, samples: list[object] | None = None,
                    data_type: str | None = None) -> tuple[str | None, str | None]:
    """Returns (category, basis).

    Name evidence beats sampled values, because a column called `email` is
    personal data even when the sample happens to be empty. Value evidence is
    only consulted for text columns -- see TEXT_TYPE_MARKERS for why.
    """
    lowered = name.lower()
    for pattern, category in PII_NAME_PATTERNS:
        if re.search(pattern, lowered):
            return category, "name"

    if not samples:
        return None, None
    if data_type is not None and not is_text_type(data_type):
        return None, None

    values = [str(v).strip() for v in samples if v is not None and str(v).strip()]
    if len(values) < 5:
        # Too few non-null values to distinguish a pattern from a coincidence.
        return None, None

    for pattern, category in PII_VALUE_PATTERNS:
        matched = sum(1 for v in values if pattern.match(v))
        if matched / len(values) >= VALUE_MATCH_THRESHOLD:
            return category, "sampled_values"

    for pattern, category in PII_SUBSTRING_PATTERNS:
        matched = sum(1 for v in values if pattern.search(v))
        if matched / len(values) >= SUBSTRING_MATCH_THRESHOLD:
            return category, "sampled_values"
    return None, None


class SchemaCrawler:
    """Introspects a live connection. Never writes to the source."""

    def __init__(self, engine: AsyncEngine, *, sample_values: bool = True,
                 include_schemas: list[str] | None = None) -> None:
        self.engine = engine
        self.sample_values = sample_values
        self.include_schemas = include_schemas

    async def crawl(self) -> CrawlResult:
        result = CrawlResult()
        async with self.engine.connect() as connection:
            tables = await connection.run_sync(self._reflect)

            for schema, name, columns in tables:
                if schema and schema.lower() in SYSTEM_SCHEMAS:
                    result.skipped.append(f"{schema}.{name}")
                    continue

                infos: list[ColumnInfo] = []
                for column in columns:
                    samples = None
                    if self.sample_values and is_text_type(str(column["type"])):
                        try:
                            samples = await self._sample(connection, name,
                                                         column["name"], schema)
                        except Exception:
                            # A column we cannot read is not a crawl failure;
                            # it just gets classified on its name alone.
                            samples = None
                    category, basis = classify_column(
                        column["name"], samples, str(column["type"]))
                    infos.append(ColumnInfo(
                        name=column["name"],
                        data_type=str(column["type"]),
                        nullable=bool(column.get("nullable", True)),
                        is_primary_key=bool(column.get("primary_key", False)),
                        pii_category=category, pii_basis=basis))

                approx = await self._approx_rows(connection, name, schema)
                result.tables.append(TableInfo(name=name, schema=schema,
                                               columns=infos, approx_rows=approx))

        if not result.tables:
            result.warnings.append(
                "No tables were discovered. Check that the credential has "
                "USAGE on the schema and SELECT on its tables.")
        if self.sample_values:
            result.warnings.append(
                "PII flags are heuristic: they are a starting point for review, "
                "not a guarantee that every personal-data column was found.")
        return result

    # --- internals --------------------------------------------------------
    def _reflect(self, sync_connection) -> list[tuple[str | None, str, list[dict]]]:
        inspector = inspect(sync_connection)
        schemas = self.include_schemas or [None]
        found: list[tuple[str | None, str, list[dict]]] = []
        for schema in schemas:
            for table in inspector.get_table_names(schema=schema):
                columns = inspector.get_columns(table, schema=schema)
                found.append((schema, table, columns))
        return found

    async def _sample(self, connection, table: str, column: str,
                      schema: str | None) -> list[object]:
        qualified = f"{schema}.{table}" if schema else table
        # Identifiers come from the inspector, not from user input, so they are
        # already the source's own names -- but they are still quoted rather
        # than interpolated raw.
        preparer = self.engine.dialect.identifier_preparer
        stmt = text(
            f"SELECT {preparer.quote(column)} FROM {preparer.quote(qualified)} "  # noqa: S608
            f"LIMIT {SAMPLE_ROWS}")
        cursor = await connection.execute(stmt)
        return [row[0] for row in cursor.fetchall()]

    async def _approx_rows(self, connection, table: str, schema: str | None) -> int:
        """Planner statistics where available; a capped COUNT otherwise.

        A full COUNT(*) on every table turns discovery into a load test, so it
        is only used on backends with no statistics view, and even then it is
        bounded.
        """
        dialect = self.engine.dialect.name
        try:
            if dialect == "postgresql":
                cursor = await connection.execute(text(
                    "SELECT reltuples::bigint FROM pg_class "
                    "WHERE oid = to_regclass(:name)"), {"name": table})
                value = cursor.scalar()
                if value is not None and value >= 0:
                    return int(value)
            preparer = self.engine.dialect.identifier_preparer
            qualified = f"{schema}.{table}" if schema else table
            cursor = await connection.execute(text(
                f"SELECT COUNT(*) FROM (SELECT 1 FROM {preparer.quote(qualified)} "  # noqa: S608
                f"LIMIT 1000000) AS bounded"))
            return int(cursor.scalar() or 0)
        except Exception:
            return 0
