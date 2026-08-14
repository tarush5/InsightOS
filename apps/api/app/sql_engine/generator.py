"""
Text-to-SQL (spec sections 17-19).

The model proposes; the validator disposes. Generated SQL is never executed
because it looked plausible — it goes through the same AST allowlist, catalog
check and LIMIT injection as anything else, and the executor still runs it in a
read-only transaction. That layering is the whole reason it is safe to let a
model write SQL at all.

Failed validation is fed back as a repair prompt rather than discarded. The
validator's findings are already written as instructions ("Column 'x' does not
exist on 'orders'. Did you mean 'y'?"), so they make good repair input — a
model given the specific rule it broke fixes it far more often than one told
only that something was wrong. Attempts are capped, and every attempt is
recorded so a caller can see what was tried.

Two hard boundaries:

* **The schema context is built from the crawled catalog**, never from the
  question. A model cannot widen its own access by asking about a table it was
  not told exists — the catalog check rejects it regardless of what the prompt
  contained.
* **With no provider configured, this returns unavailable rather than
  guessing.** A fabricated query that happens to parse is worse than an honest
  "I can't answer that without a model", because the first one returns numbers.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.llm.gateway import LLMGateway, TaskKind
from app.sql_engine.validator import SQLValidator, ValidationResult

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

SYSTEM_PROMPT = """You write read-only SQL for a business analytics system.

Rules, all enforced by a validator after you respond:
- Exactly one SELECT statement. No INSERT, UPDATE, DELETE, DDL, or stacked statements.
- Only the tables and columns listed in the schema. Never invent a name.
- Never reference system catalogs (pg_catalog, information_schema, sqlite_master).
- Aggregate rather than returning raw rows. Always GROUP BY every non-aggregated column.
- Prefer explicit JOIN ... ON. Never write a cartesian join.
- Use the exact column spellings from the schema.

Return the SQL only."""


@dataclass(slots=True)
class Attempt:
    sql: str
    ok: bool
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"sql": self.sql, "ok": self.ok, "errors": self.errors}


@dataclass(slots=True)
class GenerationResult:
    ok: bool
    sql: str | None
    attempts: list[Attempt]
    validation: ValidationResult | None = None
    reason: str = ""
    degraded: bool = False

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "sql": self.sql, "reason": self.reason,
                "degraded": self.degraded, "attempts": [a.as_dict() for a in self.attempts]}


def build_schema_context(catalog, *, max_tables: int = 40,
                         max_columns: int = 40) -> str:
    """Render the catalog compactly for a prompt.

    Truncation is stated in the output rather than hidden. A model told it is
    seeing part of the schema can say it lacks the table it needs; a model shown
    a silently trimmed list will confidently use the wrong one.
    """
    if catalog is None or not getattr(catalog, "tables", None):
        return "(no tables available)"

    names = sorted(catalog.tables)
    lines: list[str] = []
    for name in names[:max_tables]:
        spec = catalog.get(name)
        if spec is None:
            continue
        columns = sorted(spec.columns)
        shown = columns[:max_columns]
        suffix = (f", … {len(columns) - max_columns} more columns"
                  if len(columns) > max_columns else "")
        lines.append(f"{name} (~{spec.approx_rows:,} rows): "
                     f"{', '.join(shown)}{suffix}")
    if len(names) > max_tables:
        lines.append(f"… and {len(names) - max_tables} further tables not shown. "
                     f"If the one you need is missing, say so instead of guessing.")
    return "\n".join(lines)


def extract_sql(text: str) -> str:
    """Pull SQL out of whatever the model returned, or "" if there is none.

    Returning the prose unchanged when no statement is present would send
    "I'm not sure how to help" to the validator, which rejects it as a parse
    error -- a confusing diagnosis for what is really an empty answer. An empty
    string routes to a prompt that asks for SQL specifically.
    """
    body = text.strip()
    fenced = re.search(r"```(?:sql)?\s*(.+?)```", body, re.S | re.I)
    if fenced:
        body = fenced.group(1)
    # Models sometimes prefix a sentence before the statement, so the keyword
    # is looked for at the start of a line rather than anywhere in the text:
    # an unanchored search matches the ordinary English "with" in a sentence
    # like "I'm not sure how to help with that", and returns "with that." as
    # if it were a query.
    match = re.search(r"(?:\A|\n)[ \t]*(WITH|SELECT)\b", body, re.I)
    if not match:
        return ""
    return body[match.start(1):].strip().rstrip(";").strip()


class SQLGenerator:
    """Generates SQL from a question, repairing against validator feedback."""

    def __init__(self, validator: SQLValidator, *,
                 gateway: LLMGateway | None = None,
                 max_attempts: int = MAX_ATTEMPTS) -> None:
        self.validator = validator
        self.gateway = gateway or LLMGateway()
        self.max_attempts = max_attempts

    async def generate(self, question: str, *, hints: str = "") -> GenerationResult:
        schema = build_schema_context(self.validator.catalog)
        attempts: list[Attempt] = []
        prompt = self._first_prompt(question, schema, hints)

        for attempt_number in range(1, self.max_attempts + 1):
            response = await self.gateway.complete(
                task=TaskKind.SQL_REPAIR if attempt_number > 1 else TaskKind.PLANNING,
                system=SYSTEM_PROMPT, prompt=prompt, max_tokens=700,
                complexity=0.6 if attempt_number == 1 else 0.8,
                fallback="")

            if response.degraded:
                # No provider, or the call failed. Returning a guess here would
                # produce numbers nobody can trace to a question.
                return GenerationResult(
                    ok=False, sql=None, attempts=attempts, degraded=True,
                    reason="Text-to-SQL needs a language model, and none is "
                           "configured or reachable. Ask about a governed metric "
                           "instead — those are computed without one.")

            if "UNANSWERABLE" in response.text.upper():
                # The model was told it could refuse, so a refusal is a valid
                # outcome rather than a failed attempt. Treating it as one more
                # parse failure would burn the remaining retries pushing a model
                # to invent a table it has already said does not exist.
                return GenerationResult(
                    ok=False, sql=None, attempts=attempts,
                    reason="The available schema does not contain what this "
                           "question needs. Connect the relevant data source or "
                           "rephrase against the tables listed in the catalog.")

            sql = extract_sql(response.text)
            if not sql:
                attempts.append(Attempt("", False, ["The model returned no SQL."]))
                prompt = self._repair_prompt(question, schema, "",
                                             ["No SQL statement was found in your reply."])
                continue

            validation = self.validator.validate(sql)
            errors = [f"{f.rule}: {f.message}"
                      + (f" {f.hint}" if getattr(f, "hint", "") else "")
                      for f in validation.findings
                      if str(getattr(f.severity, "value", f.severity)) == "error"]
            attempts.append(Attempt(sql, validation.ok, errors))

            if validation.ok:
                log.info("text_to_sql.ok attempts=%d tables=%s",
                         attempt_number, ",".join(validation.referenced_tables))
                return GenerationResult(ok=True, sql=validation.safe_sql or sql,
                                        attempts=attempts, validation=validation)

            log.info("text_to_sql.rejected attempt=%d errors=%d",
                     attempt_number, len(errors))
            prompt = self._repair_prompt(question, schema, sql, errors)

        return GenerationResult(
            ok=False, sql=None, attempts=attempts,
            reason=f"Could not produce a valid query in {self.max_attempts} "
                   "attempts. The last errors were: "
                   + "; ".join(attempts[-1].errors[:3] if attempts else []))

    # --- prompts ----------------------------------------------------------
    @staticmethod
    def _first_prompt(question: str, schema: str, hints: str) -> str:
        extra = f"\n\nAdditional context:\n{hints}" if hints else ""
        return (f"Schema:\n{schema}\n\nQuestion: {question}{extra}\n\n"
                "Write one SELECT statement that answers it.")

    @staticmethod
    def _repair_prompt(question: str, schema: str, sql: str,
                       errors: list[str]) -> str:
        # The rejected SQL is included verbatim: a model shown only the error
        # tends to rewrite from scratch and reintroduce a different mistake.
        return (f"Schema:\n{schema}\n\nQuestion: {question}\n\n"
                f"Your previous query was rejected:\n{sql}\n\n"
                f"The validator reported:\n"
                + "\n".join(f"- {e}" for e in errors)
                + "\n\nFix exactly these problems and return the corrected "
                  "SELECT statement only. If the schema genuinely lacks what "
                  "the question needs, reply with the single word UNANSWERABLE.")
