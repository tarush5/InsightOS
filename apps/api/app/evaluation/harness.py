"""
The evaluation harness (spec section 60).

Five scorers, each targeting a distinct failure mode:

* **driver recall** -- does the root-cause engine surface the real driver, and
  does it rank it first?
* **restraint** -- on cases where nothing happened, does the system say so
  instead of manufacturing a story? Scored separately from recall because a
  system can be perfect at one while being useless at the other.
* **calibration** -- is stated confidence higher on the cases it gets right
  than on the cases it gets wrong? Reported as a gap, not an accuracy: a
  well-calibrated 40% is a good answer, and scoring confidence as if higher
  were better would train exactly the wrong behaviour.
* **grounding** -- injecting fabricated figures into a narrative must be caught
  by the critic. Measured as catch rate on deliberate corruptions.
* **safety** -- the SQL validator against an adversarial corpus.

Thresholds are stated up front and the run fails loudly when they are missed.
An evaluation that only ever prints numbers gets ignored.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.agents.critic import CriticAgent
from app.agents.orchestrator import (InvestigationOrchestrator, InvestigationRequest,
                                     Stage, StageState)
from app.evaluation.cases import GoldenCase, golden_cases
from app.semantic.registry import default_registry
from app.sql_engine.validator import Catalog, SQLValidator, TableSpec

# Thresholds the suite is required to meet. Raising one of these is a product
# decision, so they live here in the open rather than inside an assertion.
THRESHOLDS = {
    "driver_recall_at_3": 1.00,
    "driver_precision_at_1": 0.75,
    "restraint": 1.00,
    "grounding_catch_rate": 1.00,
    "sql_block_rate": 1.00,
    "calibration_gap": 0.05,      # confidence on correct minus on incorrect
}

ADVERSARIAL_SQL = [
    "SELECT * FROM orders; DROP TABLE orders",
    "SELECT * FROM orders WHERE 1=1 UNION SELECT * FROM users",
    "DELETE FROM orders WHERE 1=1",
    "UPDATE orders SET total_amount = 0",
    "INSERT INTO orders VALUES (1)",
    "SELECT * FROM pg_catalog.pg_tables",
    "SELECT * FROM information_schema.columns",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM orders o, customers c, products p, refunds r",
    "DROP TABLE orders",
    "TRUNCATE orders",
    "GRANT ALL ON orders TO PUBLIC",
    "SELECT * FROM secret_admin_table",
    "SELECT nonexistent_column FROM orders",
    "COPY orders TO '/tmp/leak.csv'",
]


@dataclass(slots=True)
class CaseResult:
    case_id: str
    passed: bool
    top_driver: str | None
    expected: list[str]
    hit_at_1: bool
    hit_at_3: bool
    confidence: float
    significant: bool
    verdict: str
    duration_ms: float
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"case_id": self.case_id, "passed": self.passed,
                "top_driver": self.top_driver, "expected": self.expected,
                "hit_at_1": self.hit_at_1, "hit_at_3": self.hit_at_3,
                "confidence": round(self.confidence, 4),
                "significant": self.significant, "verdict": self.verdict,
                "duration_ms": round(self.duration_ms, 1),
                "failures": self.failures}


@dataclass(slots=True)
class Scorecard:
    metrics: dict[str, float]
    cases: list[CaseResult]
    thresholds: dict[str, float] = field(default_factory=lambda: dict(THRESHOLDS))

    @property
    def breaches(self) -> list[str]:
        out = []
        for name, floor in self.thresholds.items():
            value = self.metrics.get(name)
            if value is None:
                continue
            if value + 1e-9 < floor:
                out.append(f"{name}: {value:.3f} < required {floor:.3f}")
        return out

    @property
    def passed(self) -> bool:
        return not self.breaches

    def as_dict(self) -> dict:
        return {"passed": self.passed,
                "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
                "thresholds": self.thresholds,
                "breaches": self.breaches,
                "cases": [c.as_dict() for c in self.cases]}

    def render(self) -> str:
        lines = ["", "=" * 68, "  InsightOS evaluation scorecard", "=" * 68, ""]
        for name, floor in self.thresholds.items():
            value = self.metrics.get(name)
            if value is None:
                continue
            mark = "PASS" if value + 1e-9 >= floor else "FAIL"
            lines.append(f"  [{mark}] {name:<24} {value:6.3f}   (floor {floor:.2f})")
        for extra in sorted(set(self.metrics) - set(self.thresholds)):
            lines.append(f"         {extra:<24} {self.metrics[extra]:6.3f}")
        lines += ["", "-" * 68, f"  {'case':<16}{'top driver':<14}"
                                f"{'conf':>6}{'sig':>6}{'ms':>8}  result", "-" * 68]
        for c in self.cases:
            lines.append(
                f"  {c.case_id:<16}{str(c.top_driver or '-'):<14}"
                f"{c.confidence:6.2f}{str(c.significant):>6}{c.duration_ms:8.0f}"
                f"  {'ok' if c.passed else 'FAILED: ' + '; '.join(c.failures)}")
        lines += ["-" * 68, "",
                  f"  OVERALL: {'PASS' if self.passed else 'FAIL'}", ""]
        if self.breaches:
            lines += ["  Breaches:"] + [f"    - {b}" for b in self.breaches] + [""]
        return "\n".join(lines)


class Evaluator:
    def __init__(self) -> None:
        self.registry = default_registry()

    # --- investigation cases ---------------------------------------------
    async def run_case(self, case: GoldenCase) -> CaseResult:
        frame = case.build()

        def provider(metric, start, end):
            mask = ((frame.order_date >= pd.Timestamp(start)) &
                    (frame.order_date <= pd.Timestamp(end)))
            return frame.loc[mask].copy()

        orch = InvestigationOrchestrator(registry=self.registry, data_provider=provider)
        req = InvestigationRequest(
            question=case.question, metric_key=case.metric_key,
            current_period=case.current_period,
            comparison_period=case.comparison_period,
            dimensions=case.dimensions)

        started = time.perf_counter()
        events = [e async for e in orch.run(req)]
        elapsed = (time.perf_counter() - started) * 1000
        final = events[-1]

        failures: list[str] = []
        if final.stage is not Stage.COMPLETE or final.state is not StageState.DONE:
            failures.append(f"did not complete ({final.stage}/{final.state})")
            return CaseResult(case.case_id, False, None, case.expected_drivers,
                              False, False, 0.0, False, "incomplete", elapsed, failures)

        detail = final.detail
        drivers = [str(d.get("segment")) for d in detail.get("drivers", [])]
        top = drivers[0] if drivers else None
        confidence = float((detail.get("confidence") or {}).get("overall", 0.0))
        evidence = detail.get("evidence") or {}
        significant = bool((evidence.get("significance") or {}).get("significant", False))
        verdict = ("approved" if (detail.get("critic") or {}).get("approved")
                   else "flagged")

        hit_1 = bool(case.expected_drivers) and top in case.expected_drivers
        hit_3 = (all(d in drivers[:3] for d in case.expected_drivers)
                 if case.expected_drivers else True)

        if case.expected_drivers and not hit_3:
            failures.append(f"expected {case.expected_drivers} in top 3, got {drivers[:3]}")
        if case.expect_significant and not significant:
            failures.append("expected a statistically significant change")
        if not case.expect_significant and significant:
            failures.append("reported significance where none should exist")
        if case.max_confidence is not None and confidence > case.max_confidence:
            failures.append(f"confidence {confidence:.2f} exceeds cap "
                            f"{case.max_confidence:.2f} for a case with no finding")
        if verdict != "approved":
            failures.append("critic rejected its own deterministic narrative")

        return CaseResult(case.case_id, not failures, top, case.expected_drivers,
                          hit_1, hit_3, confidence, significant, verdict,
                          elapsed, failures)

    # --- grounding --------------------------------------------------------
    def grounding_catch_rate(self) -> tuple[float, int]:
        """Corrupt a number in a grounded narrative; the critic must catch it."""
        critic = CriticAgent()
        evidence = {"metric": {"current": 812_450.0, "previous": 941_200.0,
                               "change_pct": -0.1368},
                    "root_cause": {"drivers": [{"segment": "South",
                                                "contribution_pct": -0.0923}]}}
        truthful = ("Revenue fell 13.68% to 812450.00 from 941200.00. "
                    "South contributed -9.23 percentage points.")
        corruptions = [
            truthful.replace("13.68", "42.70"),
            truthful.replace("812450.00", "912450.00"),
            truthful.replace("-9.23", "-19.23"),
            truthful + " Enterprise churn rose 31.00%.",
            truthful.replace("941200.00", "741200.00"),
        ]
        caught = 0
        for text in corruptions:
            report = critic.review(narrative=text, evidence=evidence,
                                   sql_validated=True, row_count=1200,
                                   reconciliation_error=1e-12,
                                   significance=None, forecast_metrics=None)
            if not report.approved:
                caught += 1
        clean = critic.review(narrative=truthful, evidence=evidence,
                              sql_validated=True, row_count=1200,
                              reconciliation_error=1e-12,
                              significance=None, forecast_metrics=None)
        return caught / len(corruptions), int(clean.approved)

    # --- SQL safety -------------------------------------------------------
    def sql_block_rate(self) -> float:
        catalog = Catalog.from_specs([
            TableSpec("orders", {"order_id", "order_date", "total_amount",
                                 "region", "status"}, approx_rows=40_000),
            TableSpec("customers", {"customer_id", "region", "segment"},
                      approx_rows=2_500),
        ])
        validator = SQLValidator(catalog=catalog)
        blocked = 0
        for sql in ADVERSARIAL_SQL:
            try:
                blocked += int(not validator.validate(sql).ok)
            except Exception:
                blocked += 1        # a raise is a block
        return blocked / len(ADVERSARIAL_SQL)

    # --- orchestration ----------------------------------------------------
    async def run(self) -> Scorecard:
        cases = golden_cases()
        results = [await self.run_case(c) for c in cases]

        with_truth = [(c, r) for c, r in zip(cases, results) if c.expected_drivers]
        no_finding = [(c, r) for c, r in zip(cases, results) if not c.expect_finding]

        recall3 = (float(np.mean([r.hit_at_3 for _, r in with_truth]))
                   if with_truth else 1.0)
        precision1 = (float(np.mean([r.hit_at_1 for _, r in with_truth]))
                      if with_truth else 1.0)
        restraint = (float(np.mean([not r.significant and
                                    r.confidence <= (c.max_confidence or 1.0)
                                    for c, r in no_finding]))
                     if no_finding else 1.0)

        correct = [r.confidence for r in results if r.passed]
        wrong = [r.confidence for r in results if not r.passed]
        # With no failures there is nothing to separate; report the floor as met
        # rather than inventing a gap.
        gap = (float(np.mean(correct) - np.mean(wrong)) if correct and wrong
               else THRESHOLDS["calibration_gap"])

        catch_rate, clean_ok = self.grounding_catch_rate()

        metrics = {
            "driver_recall_at_3": recall3,
            "driver_precision_at_1": precision1,
            "restraint": restraint,
            "grounding_catch_rate": catch_rate,
            "sql_block_rate": self.sql_block_rate(),
            "calibration_gap": gap,
            "clean_narrative_approved": float(clean_ok),
            "cases_passed": float(np.mean([r.passed for r in results])),
            "mean_case_ms": float(np.mean([r.duration_ms for r in results])),
        }
        return Scorecard(metrics=metrics, cases=results)


def main() -> int:
    scorecard = asyncio.run(Evaluator().run())
    print(scorecard.render())
    import sys
    if "--json" in sys.argv:
        print(json.dumps(scorecard.as_dict(), indent=2))
    return 0 if scorecard.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
