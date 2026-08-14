"""
SQL validation layer (spec sections 14-15, 41, 62).

Every statement produced by an agent passes through here before it can reach a
database connection. The validator is deliberately an allowlist: anything it does
not explicitly understand is rejected.

Design notes
------------
* Parsing uses sqlglot's AST, never regex. Regex-based SQL guards are trivially
  defeated by comments, string literals and unicode escapes.
* Table/column authorization is resolved against the catalog the workspace has
  actually ingested, so a hallucinated table name fails closed rather than
  leaking a database error to the model.
* A LIMIT is injected rather than merely checked, so an agent cannot cause a
  full-table scan by omitting one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import sqlglot
from sqlglot import exp

from app.core.config import settings

# Statement types that may ever reach a data source. Everything else -- INSERT,
# UPDATE, DELETE, DROP, TRUNCATE, GRANT, COPY, CALL, SET, VACUUM -- is refused.
_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Except, exp.Intersect, exp.Subquery)

# Functions that read the filesystem, reach the network, or escalate privileges.
_BANNED_FUNCTIONS = {
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_sleep",
    "lo_import", "lo_export", "dblink", "dblink_exec", "copy",
    "load_file", "sys_exec", "sys_eval", "xp_cmdshell", "sp_executesql",
    "query_to_xml", "pg_stat_file", "current_setting", "set_config",
    "load_extension", "readfile", "writefile", "eval", "system",
}

# Schemas that hold credentials, roles, or engine internals.
_BANNED_SCHEMAS = {
    "pg_catalog", "information_schema", "pg_toast", "pg_temp",
    "mysql", "performance_schema", "sys", "sqlite_master", "sqlite_temp_master",
}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class Rule(StrEnum):
    PARSE_FAILED = "parse_failed"
    MULTIPLE_STATEMENTS = "multiple_statements"
    NON_READ_STATEMENT = "non_read_statement"
    UNKNOWN_TABLE = "unknown_table"
    UNKNOWN_COLUMN = "unknown_column"
    UNAUTHORIZED_TABLE = "unauthorized_table"
    BANNED_SCHEMA = "banned_schema"
    BANNED_FUNCTION = "banned_function"
    CARTESIAN_JOIN = "cartesian_join"
    TOO_MANY_JOINS = "too_many_joins"
    TOO_DEEP = "excessive_nesting"
    UNGROUPED_SELECT = "ungrouped_aggregate_select"
    NO_LIMIT = "missing_limit"
    SELECT_STAR = "select_star"


@dataclass(frozen=True, slots=True)
class Finding:
    rule: Rule
    severity: Severity
    message: str
    hint: str = ""


@dataclass(slots=True)
class TableSpec:
    """One physical table the workspace has ingested and is allowed to read."""
    name: str
    columns: set[str]
    approx_rows: int = 0
    schema: str = "public"

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass(slots=True)
class Catalog:
    """Workspace-scoped view of readable tables. Tenant isolation starts here."""
    tables: dict[str, TableSpec] = field(default_factory=dict)

    @classmethod
    def from_specs(cls, specs: list[TableSpec]) -> "Catalog":
        return cls(tables={s.name.lower(): s for s in specs})

    def get(self, name: str) -> TableSpec | None:
        return self.tables.get(name.lower().split(".")[-1])

    def columns_of(self, name: str) -> set[str]:
        spec = self.get(name)
        return spec.columns if spec else set()


@dataclass(slots=True)
class ValidationResult:
    ok: bool
    findings: list[Finding]
    safe_sql: str | None
    referenced_tables: list[str]
    estimated_scanned_rows: int
    join_count: int
    depth: int

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    @property
    def error_summary(self) -> str:
        return "; ".join(f"[{f.rule}] {f.message}" for f in self.errors)


class SQLValidator:
    """Validates and rewrites a single read-only statement."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        dialect: str = "postgres",
        max_rows: int | None = None,
        max_joins: int | None = None,
        max_depth: int | None = None,
    ) -> None:
        self.catalog = catalog
        self.dialect = dialect
        self.max_rows = max_rows or settings.SQL_MAX_RESULT_ROWS
        self.max_joins = max_joins or settings.SQL_MAX_JOINS
        self.max_depth = max_depth or settings.SQL_MAX_QUERY_DEPTH

    # -- public ---------------------------------------------------------------

    def validate(self, sql: str) -> ValidationResult:
        findings: list[Finding] = []

        try:
            statements = [s for s in sqlglot.parse(sql, dialect=self.dialect) if s is not None]
        except Exception as exc:  # sqlglot raises several parse error types
            return self._fail(Rule.PARSE_FAILED, f"Could not parse SQL: {exc}",
                              hint="Check for unbalanced quotes or parentheses.")

        if not statements:
            return self._fail(Rule.PARSE_FAILED, "No statement found.")
        if len(statements) > 1:
            return self._fail(
                Rule.MULTIPLE_STATEMENTS,
                f"{len(statements)} statements submitted; exactly one is allowed.",
                hint="Stacked statements are a common SQL-injection vector.",
            )

        tree = statements[0]
        if not isinstance(tree, _ALLOWED_ROOTS):
            return self._fail(
                Rule.NON_READ_STATEMENT,
                f"{type(tree).__name__.upper()} is not permitted. InsightOS is read-only.",
                hint="Only SELECT / WITH ... SELECT statements can be executed.",
            )

        findings += self._check_tables(tree)
        findings += self._check_functions(tree)
        findings += self._check_columns(tree)
        joins, join_findings = self._check_joins(tree)
        findings += join_findings
        depth, depth_findings = self._check_depth(tree)
        findings += depth_findings
        findings += self._check_aggregation(tree)

        tables = sorted({t.name.lower() for t in tree.find_all(exp.Table) if t.name})
        scanned = sum((self.catalog.get(t).approx_rows if self.catalog.get(t) else 0) for t in tables)

        has_errors = any(f.severity is Severity.ERROR for f in findings)
        safe_sql = None
        if not has_errors:
            safe_sql, limit_finding = self._enforce_limit(tree)
            if limit_finding:
                findings.append(limit_finding)

        return ValidationResult(
            ok=not has_errors,
            findings=findings,
            safe_sql=safe_sql,
            referenced_tables=tables,
            estimated_scanned_rows=scanned,
            join_count=joins,
            depth=depth,
        )

    # -- individual rules -----------------------------------------------------

    def _check_tables(self, tree: exp.Expression) -> list[Finding]:
        out: list[Finding] = []
        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}

        for table in tree.find_all(exp.Table):
            name = (table.name or "").lower()
            schema = (table.db or "").lower()
            if not name or name in cte_names:
                continue
            if schema in _BANNED_SCHEMAS or name in _BANNED_SCHEMAS:
                out.append(Finding(
                    Rule.BANNED_SCHEMA, Severity.ERROR,
                    f"Access to system catalog '{schema or name}' is blocked.",
                    hint="Use the /datasets endpoints to inspect schema instead.",
                ))
                continue
            if self.catalog.get(name) is None:
                near = self._nearest(name)
                out.append(Finding(
                    Rule.UNKNOWN_TABLE, Severity.ERROR,
                    f"Table '{name}' is not in this workspace's catalog.",
                    hint=f"Did you mean '{near}'?" if near else "Call list_tables first.",
                ))
        return out

    def _check_functions(self, tree: exp.Expression) -> list[Finding]:
        out: list[Finding] = []
        for node in tree.find_all(exp.Anonymous, exp.Func):
            fname = (getattr(node, "name", "") or node.sql_name()).lower()
            if fname in _BANNED_FUNCTIONS:
                out.append(Finding(
                    Rule.BANNED_FUNCTION, Severity.ERROR,
                    f"Function '{fname}' is not permitted.",
                    hint="This function can read files, sleep, or reach the network.",
                ))
        return out

    def _check_columns(self, tree: exp.Expression) -> list[Finding]:
        """Only validate columns we can unambiguously attribute to one table."""
        out: list[Finding] = []
        alias_map: dict[str, str] = {}
        for table in tree.find_all(exp.Table):
            if not table.name:
                continue
            alias_map[(table.alias or table.name).lower()] = table.name.lower()

        if any(isinstance(s, exp.Star) for s in tree.find_all(exp.Star)):
            out.append(Finding(
                Rule.SELECT_STAR, Severity.WARNING,
                "SELECT * scans every column.",
                hint="Project only the columns the analysis needs.",
            ))

        # Unqualified columns are checked against the union of every referenced
        # table, but only when the whole FROM clause resolves to catalog tables.
        # A CTE or derived table introduces names the catalog has never seen, and
        # flagging those would reject valid SQL -- so in that case unqualified
        # columns are left alone and the qualified checks below still apply.
        resolvable = bool(alias_map) and not list(tree.find_all(exp.CTE))
        union: set[str] = set()
        for physical in set(alias_map.values()):
            spec = self.catalog.get(physical)
            if spec is None:
                resolvable = False
                break
            union |= {c.lower() for c in spec.columns}

        # Output aliases are legal references in ORDER BY / HAVING and never
        # appear in the catalog.
        #
        # A self-alias is excluded. `SELECT category AS category` defines an
        # alias whose name is the column it selects, so exempting it would let
        # any unknown column through simply by aliasing it to itself -- which is
        # exactly what a generated projection does. The alias only earns the
        # exemption when it renames something.
        aliases = {
            a.alias.lower() for a in tree.find_all(exp.Alias)
            if a.alias and not (isinstance(a.this, exp.Column)
                                and a.this.name.lower() == a.alias.lower())
        }

        for col in tree.find_all(exp.Column):
            tbl_ref = (col.table or "").lower()
            name = col.name.lower()
            if not tbl_ref:
                if resolvable and name not in union and name not in aliases:
                    out.append(Finding(
                        Rule.UNKNOWN_COLUMN, Severity.ERROR,
                        f"Column '{col.name}' does not exist on any referenced "
                        f"table ({', '.join(sorted(set(alias_map.values())))}).",
                        hint=self._nearest_column(name, union),
                    ))
                continue
            physical = alias_map.get(tbl_ref)
            if physical is None:
                continue
            spec = self.catalog.get(physical)
            if spec is None:
                continue
            if name not in {c.lower() for c in spec.columns}:
                out.append(Finding(
                    Rule.UNKNOWN_COLUMN, Severity.ERROR,
                    f"Column '{col.name}' does not exist on '{physical}'.",
                    hint=self._nearest_column(name, {c.lower() for c in spec.columns})
                         or f"Available: {', '.join(sorted(spec.columns)[:8])}...",
                ))
        return out

    @staticmethod
    def _nearest_column(name: str, candidates: set[str]) -> str:
        """A near-miss is usually a typo or a hallucinated field; naming the
        closest real column turns a dead end into a one-word fix."""
        import difflib

        match = difflib.get_close_matches(name, sorted(candidates), n=1, cutoff=0.7)
        if match:
            return f"Did you mean '{match[0]}'?"
        return (f"Available: {', '.join(sorted(candidates)[:8])}..."
                if candidates else "")

    def _check_joins(self, tree: exp.Expression) -> tuple[int, list[Finding]]:
        out: list[Finding] = []
        joins = list(tree.find_all(exp.Join))
        if len(joins) > self.max_joins:
            out.append(Finding(
                Rule.TOO_MANY_JOINS, Severity.ERROR,
                f"{len(joins)} joins exceeds the limit of {self.max_joins}.",
                hint="Split the analysis into staged queries.",
            ))
        for join in joins:
            if not join.args.get("on") and not join.args.get("using"):
                kind = (join.side or join.kind or "").upper()
                if kind not in {"CROSS"}:
                    out.append(Finding(
                        Rule.CARTESIAN_JOIN, Severity.ERROR,
                        "Join has no ON or USING clause, producing a cartesian product.",
                        hint="Add the foreign-key condition from the relationship graph.",
                    ))
        return len(joins), out

    def _check_depth(self, tree: exp.Expression) -> tuple[int, list[Finding]]:
        def depth_of(node: exp.Expression, current: int = 0) -> int:
            deepest = current
            for child in node.args.values():
                items = child if isinstance(child, list) else [child]
                for item in items:
                    if isinstance(item, exp.Expression):
                        nxt = current + 1 if isinstance(item, (exp.Subquery, exp.Select)) else current
                        deepest = max(deepest, depth_of(item, nxt))
            return deepest

        depth = depth_of(tree, 1)
        if depth > self.max_depth:
            return depth, [Finding(
                Rule.TOO_DEEP, Severity.ERROR,
                f"Query nests {depth} levels deep (limit {self.max_depth}).",
                hint="Flatten with CTEs or materialise an intermediate dataset.",
            )]
        return depth, []

    def _check_aggregation(self, tree: exp.Expression) -> list[Finding]:
        """Flag bare columns selected alongside aggregates with no GROUP BY."""
        out: list[Finding] = []
        for select in tree.find_all(exp.Select):
            if select.args.get("group"):
                continue
            projections = select.expressions
            has_agg = any(p.find(exp.AggFunc) for p in projections)
            if not has_agg:
                continue
            bare = [
                p.alias_or_name for p in projections
                if not p.find(exp.AggFunc) and p.find(exp.Column)
            ]
            if bare:
                out.append(Finding(
                    Rule.UNGROUPED_SELECT, Severity.ERROR,
                    f"Non-aggregated column(s) {bare} selected with aggregates and no GROUP BY.",
                    hint=f"Add: GROUP BY {', '.join(bare)}",
                ))
        return out

    def _enforce_limit(self, tree: exp.Expression) -> tuple[str, Finding | None]:
        """Inject or tighten the outermost LIMIT so a scan can never run unbounded."""
        finding: Finding | None = None
        target = tree.this if isinstance(tree, exp.Subquery) else tree

        existing = target.args.get("limit") if isinstance(target, exp.Select) else None
        if existing is None:
            target = target.limit(self.max_rows)
            finding = Finding(
                Rule.NO_LIMIT, Severity.WARNING,
                f"No LIMIT supplied; capped at {self.max_rows:,} rows.",
            )
        else:
            try:
                current = int(existing.expression.name)
                if current > self.max_rows:
                    target = target.limit(self.max_rows)
                    finding = Finding(
                        Rule.NO_LIMIT, Severity.WARNING,
                        f"LIMIT {current:,} reduced to {self.max_rows:,}.",
                    )
            except (AttributeError, ValueError):
                pass
        return target.sql(dialect=self.dialect, pretty=True), finding

    # -- helpers --------------------------------------------------------------

    def _nearest(self, name: str) -> str | None:
        import difflib
        matches = difflib.get_close_matches(name, list(self.catalog.tables), n=1, cutoff=0.6)
        return matches[0] if matches else None

    @staticmethod
    def _fail(rule: Rule, message: str, hint: str = "") -> ValidationResult:
        return ValidationResult(
            ok=False,
            findings=[Finding(rule, Severity.ERROR, message, hint)],
            safe_sql=None,
            referenced_tables=[],
            estimated_scanned_rows=0,
            join_count=0,
            depth=0,
        )
