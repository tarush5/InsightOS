"""
Critic agent (spec section 29).

Runs deterministic checks against the computed evidence bundle before anything
is shown to a user. The design intent: a language model wrote the narrative, so
the narrative is the untrusted artifact. Every numeric claim in it must be
traceable to a value that Python computed.

Checks that fail with ``blocking=True`` suppress the conclusion entirely and the
investigation returns "insufficient evidence" rather than a confident guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUMBER_RE = re.compile(r"(-?\d[\d,]*\.?\d*)\s*(%|percent)?")


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    blocking: bool
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed,
                "blocking": self.blocking, "detail": self.detail}


@dataclass(slots=True)
class CriticReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def blocking_failures(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.blocking)

    @property
    def approved(self) -> bool:
        return self.blocking_failures == 0

    def as_dict(self) -> dict:
        return {
            "approved": self.approved,
            "passed": self.passed_count,
            "total": self.total,
            "blocking_failures": self.blocking_failures,
            "checks": [c.as_dict() for c in self.checks],
        }


class CriticAgent:
    """Verification only. Holds no tools and cannot query data -- it judges the
    artifacts other agents produced, which keeps it cheap and hard to subvert."""

    def __init__(self, *, numeric_tolerance: float = 0.015) -> None:
        self.tolerance = numeric_tolerance

    def review(
        self,
        *,
        narrative: str,
        evidence: dict,
        sql_validated: bool,
        reconciliation_error: float,
        significance: dict | None,
        forecast_metrics: dict | None,
        row_count: int,
        retrieved_passages: list[str] | None = None,
    ) -> CriticReport:
        report = CriticReport()

        report.checks.append(Check(
            "sql_validated", sql_validated, True,
            "Every executed statement passed the read-only validator."
            if sql_validated else "A statement reached execution without validation.",
        ))

        report.checks.append(Check(
            "result_non_empty", row_count > 0, True,
            f"Query returned {row_count:,} rows."
            if row_count else "Query returned no rows; there is nothing to conclude from.",
        ))

        recon_ok = reconciliation_error < 1e-6
        report.checks.append(Check(
            "attribution_reconciles", recon_ok, True,
            f"Driver contributions sum to the headline change (residual {reconciliation_error:.2e})."
            if recon_ok else
            f"Driver contributions do not sum to the headline change "
            f"(residual {reconciliation_error:.2e}). The decomposition is unsound.",
        ))

        if retrieved_passages:
            report.checks.append(self._check_injection(narrative, retrieved_passages))

        grounded, ungrounded = self._check_numbers(narrative, evidence)
        report.checks.append(Check(
            "numbers_grounded", not ungrounded, True,
            f"All {grounded} numeric claims trace to computed values."
            if not ungrounded else
            f"Ungrounded numeric claims: {', '.join(ungrounded[:5])}. "
            "These do not appear in the computed evidence.",
        ))

        if significance is not None:
            sig = significance.get("significant", False)
            causal_language = self._causal_language(narrative)
            ok = sig or not causal_language
            report.checks.append(Check(
                "causal_claims_supported", ok, True,
                "Causal phrasing is backed by a significant test result." if ok else
                f"Narrative uses causal language ({', '.join(causal_language)}) but the "
                f"change is not statistically significant (p={significance.get('p_value')}).",
            ))
        else:
            report.checks.append(Check(
                "causal_claims_supported", not self._causal_language(narrative), True,
                "No unsupported causal language detected.",
            ))

        if forecast_metrics is not None:
            beats = forecast_metrics.get("beats_baseline", False)
            report.checks.append(Check(
                "forecast_beats_baseline", beats, False,
                "Forecast outperformed the seasonal-naive baseline in backtest."
                if beats else
                "Forecast did not beat seasonal naive; presented as a baseline projection.",
            ))

        report.checks.append(Check(
            "evidence_attached", bool(evidence.get("sql")) and bool(evidence.get("metric")),
            False,
            "SQL and metric definition are attached to the conclusion."
            if evidence.get("sql") else "Conclusion is missing part of its evidence trail.",
        ))

        return report

    # -- internals ------------------------------------------------------------

    def _check_injection(self, narrative: str, passages: list[str]) -> Check:
        """Block a narrative that acted on instructions found in a document.

        This is the layer that does not depend on recognising the phrasing of an
        attack. The scanner in `app/rag/injection.py` catches known shapes and an
        attacker who knows those rules writes around them; this check looks at
        the *outcome* instead -- whether the answer began following directions
        that appeared in retrieved content rather than answering the question.
        """
        from app.rag.injection import scan

        directives = [p for p in passages if scan(p).suspicious]
        if not directives:
            return Check("no_injected_instructions", True, True,
                         "No instruction-shaped content in the retrieved passages.")

        # The narrative echoing a flagged passage's directive language is the
        # signal that the instruction was acted on rather than ignored.
        echoed = scan(narrative)
        acted_on = echoed.suspicious
        return Check(
            "no_injected_instructions", not acted_on, True,
            (f"{len(directives)} retrieved passage(s) contain instruction-shaped "
             "text; the answer did not adopt it.") if not acted_on else
            (f"The answer reproduces directive content ({', '.join(echoed.labels)}) "
             f"found in {len(directives)} retrieved passage(s). A document cannot "
             "instruct the system, so this response is withheld."))

    def _check_numbers(self, narrative: str, evidence: dict) -> tuple[int, list[str]]:
        """Every number in the narrative must match a computed value within tolerance."""
        computed = self._flatten_numbers(evidence)
        grounded, ungrounded = 0, []
        for raw, pct in _NUMBER_RE.findall(narrative):
            try:
                value = float(raw.replace(",", ""))
            except ValueError:
                continue
            if abs(value) < 2 and not pct:
                continue  # ordinals, small counts like "3 drivers"
            candidates = [value, value / 100.0, value * 100.0] if pct else [value]
            if any(self._near(c, computed) for c in candidates):
                grounded += 1
            else:
                ungrounded.append(f"{raw}{'%' if pct else ''}")
        return grounded, ungrounded

    def _near(self, value: float, computed: list[float]) -> bool:
        for c in computed:
            scale = max(abs(c), abs(value), 1e-9)
            if abs(c - value) / scale <= self.tolerance:
                return True
        return False

    @staticmethod
    def _flatten_numbers(obj, acc: list[float] | None = None) -> list[float]:
        acc = acc if acc is not None else []
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            acc.append(float(obj))
            acc.append(abs(float(obj)))
        elif isinstance(obj, dict):
            for v in obj.values():
                CriticAgent._flatten_numbers(v, acc)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                CriticAgent._flatten_numbers(v, acc)
        return acc

    @staticmethod
    def _causal_language(text: str) -> list[str]:
        markers = ["caused by", "because of", "due to", "driven by", "as a result of",
                   "led to", "resulted in", "responsible for"]
        low = text.lower()
        return [m for m in markers if m in low]
