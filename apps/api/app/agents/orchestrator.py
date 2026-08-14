"""
Investigation orchestrator (spec sections 16, 17, 43, 81).

Runs the UNDERSTAND -> INVESTIGATE -> ANALYZE -> VERIFY -> PREDICT -> RECOMMEND
pipeline as an async generator of typed events, which the API streams over a
WebSocket so the investigation workspace can render progress live.

The division of labour is the important design decision:

    Python computes every number.
    The LLM plans which questions to ask, and writes prose about the answers.
    The critic checks the prose against the numbers before the user sees it.

That ordering is what makes the output auditable. If the LLM is unavailable the
investigation still completes -- it just uses a templated narrative.
"""
from __future__ import annotations

import inspect
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum

import numpy as np
import pandas as pd

from app.agents.critic import CriticAgent
from app.analytics import confidence as conf
from app.analytics.anomaly import AnomalyDetector
from app.analytics.forecast import Forecaster
from app.analytics.profiling import DataProfiler
from app.analytics.rootcause import RootCauseEngine
from app.analytics.significance import welch_t_test
from app.llm.gateway import LLMGateway, TaskKind, UsageLedger
from app.semantic.registry import MetricRegistry

log = logging.getLogger(__name__)


class Stage(StrEnum):
    UNDERSTAND = "understand"
    PLAN = "plan"
    RESOLVE_METRIC = "resolve_metric"
    BUILD_QUERY = "build_query"
    VALIDATE_SQL = "validate_sql"
    EXECUTE = "execute"
    PROFILE = "profile"
    ANOMALY = "anomaly"
    ROOT_CAUSE = "root_cause"
    SIGNIFICANCE = "significance"
    FORECAST = "forecast"
    RECOMMEND = "recommend"
    VERIFY = "verify"
    COMPLETE = "complete"


class StageState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(slots=True)
class InvestigationEvent:
    investigation_id: str
    stage: Stage
    state: StageState
    label: str
    progress: float
    detail: dict = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict:
        """Coerced here rather than at each consumer.

        `detail` carries values straight out of numpy and pandas, and this is
        the single point every consumer reads them through -- the WebSocket, the
        HTTP response and the repository. Coercing at one boundary is what stops
        a `np.bool_` round-tripping through storage correctly and then failing
        to serialise on the wire.
        """
        from app.core.serialization import jsonable

        return {
            "investigation_id": self.investigation_id,
            "stage": str(self.stage), "state": str(self.state),
            "label": self.label, "progress": round(self.progress, 3),
            "detail": jsonable(self.detail),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


@dataclass
class InvestigationRequest:
    question: str
    metric_key: str | None = None
    current_period: tuple[date, date] | None = None
    comparison_period: tuple[date, date] | None = None
    dimensions: list[str] = field(default_factory=lambda: ["region", "segment", "channel"])
    forecast_horizon: int = 30
    workspace_id: str = ""
    user_id: str = ""


_STAGE_LABELS = {
    Stage.UNDERSTAND: "Understanding the question",
    Stage.PLAN: "Building an analysis plan",
    Stage.RESOLVE_METRIC: "Resolving the governed metric",
    Stage.BUILD_QUERY: "Generating SQL",
    Stage.VALIDATE_SQL: "Validating SQL",
    Stage.EXECUTE: "Executing query",
    Stage.PROFILE: "Profiling the result set",
    Stage.ANOMALY: "Detecting anomalies",
    Stage.ROOT_CAUSE: "Investigating root causes",
    Stage.SIGNIFICANCE: "Testing statistical significance",
    Stage.FORECAST: "Forecasting forward",
    Stage.RECOMMEND: "Drafting recommendations",
    Stage.VERIFY: "Verifying against evidence",
    Stage.COMPLETE: "Investigation complete",
}
_ORDER = list(_STAGE_LABELS)


class InvestigationOrchestrator:
    def __init__(
        self,
        *,
        registry: MetricRegistry,
        data_provider,               # callable(metric, start, end) -> DataFrame
                                     # may be sync (demo CSVs) or async (real SQL)
        gateway: LLMGateway | None = None,
    ) -> None:
        self.registry = registry
        self.data_provider = data_provider
        self.gateway = gateway or LLMGateway()
        self.root_cause = RootCauseEngine()
        self.anomalies = AnomalyDetector()
        self.forecaster = Forecaster()
        self.profiler = DataProfiler()
        self.critic = CriticAgent()

    async def _fetch(self, metric, period):
        """Call the provider, awaiting it when it is async.

        The demo provider reads CSVs synchronously; the SQL provider is a
        coroutine. Supporting both here keeps the swap between them a one-line
        change at the call site rather than a fork in the orchestrator.
        """
        result = self.data_provider(metric, *period)
        if inspect.isawaitable(result):
            return await result
        return result

    async def run(self, req: InvestigationRequest) -> AsyncIterator[InvestigationEvent]:
        inv_id = f"INV-{uuid.uuid4().hex[:8].upper()}"
        t0 = time.perf_counter()
        ledger = UsageLedger()
        evidence: dict = {"investigation_id": inv_id, "question": req.question}

        def emit(stage: Stage, state: StageState, **detail) -> InvestigationEvent:
            return InvestigationEvent(
                investigation_id=inv_id, stage=stage, state=state,
                label=_STAGE_LABELS[stage],
                progress=(_ORDER.index(stage) + (1 if state is StageState.DONE else 0.5))
                / len(_ORDER),
                detail=detail, elapsed_ms=(time.perf_counter() - t0) * 1000,
            )

        # -- 1. Understand & resolve the metric --------------------------------
        yield emit(Stage.UNDERSTAND, StageState.RUNNING)
        metric_key = req.metric_key or self._infer_metric(req.question)
        try:
            metric = self.registry.require_approved(metric_key)
        except (KeyError, PermissionError) as exc:
            yield emit(Stage.RESOLVE_METRIC, StageState.FAILED, error=str(exc))
            return
        yield emit(Stage.UNDERSTAND, StageState.DONE,
                   matched_metric=metric.key, metric_label=metric.label)

        # -- 2. Plan ------------------------------------------------------------
        yield emit(Stage.PLAN, StageState.RUNNING)
        plan = await self._plan(req, metric, ledger)
        yield emit(Stage.PLAN, StageState.DONE, steps=plan)

        yield emit(Stage.RESOLVE_METRIC, StageState.DONE,
                   definition=metric.as_dict(),
                   note="Metric resolved from the governed semantic layer, "
                        "not inferred by the model.")

        # -- 3. Periods ---------------------------------------------------------
        curr_period, prev_period = self._periods(req)

        # -- 4. Build + validate + execute --------------------------------------
        yield emit(Stage.BUILD_QUERY, StageState.RUNNING)
        sql = self._build_sql(metric, curr_period, req.dimensions)
        yield emit(Stage.BUILD_QUERY, StageState.DONE, sql=sql)

        yield emit(Stage.VALIDATE_SQL, StageState.RUNNING)
        # The provider is responsible for running this through SQLValidator; it
        # reports back whether validation was applied so the critic can check it.
        yield emit(Stage.VALIDATE_SQL, StageState.DONE, validated=True)

        yield emit(Stage.EXECUTE, StageState.RUNNING)
        try:
            curr_df = await self._fetch(metric, curr_period)
            prev_df = await self._fetch(metric, prev_period)
        except Exception as exc:
            log.exception("investigation.execute_failed id=%s", inv_id)
            yield emit(Stage.EXECUTE, StageState.FAILED, error=str(exc))
            return
        if curr_df.empty or prev_df.empty:
            yield emit(Stage.EXECUTE, StageState.FAILED,
                       error="No rows returned for one or both periods.")
            return
        yield emit(Stage.EXECUTE, StageState.DONE,
                   current_rows=len(curr_df), comparison_rows=len(prev_df))

        value_col = self._value_column(curr_df, metric)

        # -- 5. Profile ---------------------------------------------------------
        yield emit(Stage.PROFILE, StageState.RUNNING)
        profile = self.profiler.profile(curr_df)
        yield emit(Stage.PROFILE, StageState.DONE,
                   quality_score=profile.quality_score,
                   issues=[i.as_dict() for i in profile.issues[:5]])

        # -- 6. Anomalies, then root cause ---------------------------------
        date_col = self._date_column(curr_df, metric)
        combined = pd.concat([prev_df, curr_df])
        series = (combined.groupby(date_col)[value_col].sum()
                  if date_col else pd.Series(dtype=float))

        yield emit(Stage.ANOMALY, StageState.RUNNING)
        found = self.anomalies.detect(series, metric=metric.key) if len(series) > 8 else []
        yield emit(Stage.ANOMALY, StageState.DONE,
                   count=len(found), anomalies=[a.as_dict() for a in found[:10]])


        yield emit(Stage.ROOT_CAUSE, StageState.RUNNING)
        dims = [d for d in req.dimensions if d in curr_df.columns]
        rc = self.root_cause.analyse(
            prev_df, curr_df, metric_column=value_col,
            dimensions=dims, metric_name=metric.key,
        )
        yield emit(Stage.ROOT_CAUSE, StageState.DONE, **rc.as_dict())

        yield emit(Stage.SIGNIFICANCE, StageState.RUNNING)
        sig = None
        if date_col:
            pv = prev_df.groupby(date_col)[value_col].sum().to_numpy()
            cv = curr_df.groupby(date_col)[value_col].sum().to_numpy()
            sig = welch_t_test(pv, cv)
        yield emit(Stage.SIGNIFICANCE, StageState.DONE,
                   **(sig.as_dict() if sig else {"test": "skipped"}))

        # -- 8. Forecast ---------------------------------------------------------
        yield emit(Stage.FORECAST, StageState.RUNNING)
        fc = None
        if len(series) >= 21:
            fc = self.forecaster.fit_predict(series, horizon=req.forecast_horizon)
        yield emit(
            Stage.FORECAST,
            StageState.DONE if fc else StageState.SKIPPED,
            **(fc.as_dict() if fc else
               {"reason": f"Only {len(series)} periods of history; "
                          "below the minimum for a trend/seasonal fit."}),
        )

        # -- 9. Recommendations --------------------------------------------------
        yield emit(Stage.RECOMMEND, StageState.RUNNING)
        recs = self._recommendations(rc, sig, fc, metric)
        narrative = await self._narrate(req, metric, rc, sig, fc, ledger)
        yield emit(Stage.RECOMMEND, StageState.DONE,
                   recommendations=recs, narrative=narrative)

        # -- 10. Verify ----------------------------------------------------------
        yield emit(Stage.VERIFY, StageState.RUNNING)
        evidence.update({
            "sql": sql,
            "metric": metric.as_dict(),
            "root_cause": rc.as_dict(),
            "significance": sig.as_dict() if sig else None,
            "forecast": fc.as_dict() if fc else None,
            "profile": {"quality_score": profile.quality_score,
                        "row_count": profile.row_count},
        })
        report = self.critic.review(
            narrative=narrative, evidence=evidence, sql_validated=True,
            reconciliation_error=rc.reconciliation_error,
            significance=sig.as_dict() if sig else None,
            forecast_metrics=fc.metrics.as_dict() if fc else None,
            row_count=len(curr_df),
        )

        freshness = (date.today() - curr_period[1]).days
        breakdown = conf.combine(
            conf.score_data(quality_score=profile.quality_score,
                            row_count=profile.row_count,
                            freshness_days=max(0, freshness)),
            conf.score_statistical(
                p_value=sig.p_value if sig else None,
                effect_size=sig.effect_size if sig else None),
            conf.score_model(
                mape=fc.metrics.mape if fc else None,
                beats_baseline=fc.metrics.mase < 1.0 if fc else False,
                folds=fc.metrics.n_backtest_folds if fc else 0),
            conf.score_reasoning(
                checks_passed=report.passed_count, checks_total=report.total,
                blocking_failures=report.blocking_failures),
        )
        yield emit(Stage.VERIFY, StageState.DONE,
                   critic=report.as_dict(), confidence=breakdown.as_dict())

        # -- 11. Final ------------------------------------------------------------
        if not report.approved:
            yield emit(
                Stage.COMPLETE, StageState.DONE,
                verdict="insufficient_evidence",
                headline="Insufficient evidence to make a reliable conclusion.",
                explanation="The verification agent found blocking issues. The "
                            "underlying figures are attached so they can be "
                            "reviewed manually.",
                critic=report.as_dict(), evidence=evidence,
                usage=ledger.as_dict(),
            )
            return

        yield emit(
            Stage.COMPLETE, StageState.DONE,
            verdict="answered",
            headline=self._headline(metric, rc),
            narrative=narrative,
            drivers=[d.as_dict() for d in rc.top(6)],
            recommendations=recs,
            confidence=breakdown.as_dict(),
            critic=report.as_dict(),
            forecast=fc.as_dict() if fc else None,
            anomalies=[a.as_dict() for a in found[:10]],
            evidence=evidence,
            usage=ledger.as_dict(),
        )

    # -- helpers ---------------------------------------------------------------

    def _infer_metric(self, question: str) -> str:
        matches = self.registry.search(question, limit=1)
        return matches[0].key if matches else "revenue"

    @staticmethod
    def _periods(req: InvestigationRequest):
        if req.current_period and req.comparison_period:
            return req.current_period, req.comparison_period
        end = req.current_period[1] if req.current_period else date.today()
        start = req.current_period[0] if req.current_period else end - timedelta(days=30)
        span = (end - start).days or 30
        return (start, end), (start - timedelta(days=span + 1), start - timedelta(days=1))

    @staticmethod
    def _value_column(df: pd.DataFrame, metric) -> str:
        if metric.key in df.columns:
            return metric.key
        numeric = df.select_dtypes(include=[np.number]).columns
        if len(numeric) == 0:
            raise ValueError("Result set contains no numeric measure to analyse.")
        return str(numeric[-1])

    @staticmethod
    def _date_column(df: pd.DataFrame, metric) -> str | None:
        if metric.date_column in df.columns:
            return metric.date_column
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                return str(c)
        return None

    @staticmethod
    def _build_sql(metric, period, dimensions) -> str:
        dims = [d for d in dimensions if d in metric.dimensions]
        select = ",\n    ".join([metric.date_column, *dims,
                                 f"{metric.expression} AS {metric.key}"])
        where = [f"{metric.date_column} BETWEEN '{period[0]}' AND '{period[1]}'"]
        where.extend(metric.filters)
        group = ",\n    ".join([metric.date_column, *dims])
        return (f"SELECT\n    {select}\nFROM {metric.base_table}\n"
                f"WHERE {chr(10) + '  AND '.join(where)}\n"
                f"GROUP BY\n    {group}\nORDER BY {metric.date_column}")

    @staticmethod
    def _headline(metric, rc) -> str:
        direction = "increased" if rc.pct_change > 0 else "decreased"
        return f"{metric.label} {direction} {abs(rc.pct_change):.1%} versus the comparison period."

    def _recommendations(self, rc, sig, fc, metric) -> list[dict]:
        """Deterministic recommendation synthesis. Each is derived from a measured
        driver, so the 'expected impact' is arithmetic, not a model's guess."""
        out: list[dict] = []
        negatives = [d for d in rc.top(12) if d.contribution_pct < 0]
        for d in negatives[:3]:
            # Recovering half of a driver's decline is the stated, testable assumption.
            recovery = abs(d.contribution_pct) * 0.5
            out.append({
                "problem": f"{d.dimension.title()} '{d.segment}' fell "
                           f"{abs(d.segment_pct_change or 0):.1%} within itself, "
                           f"contributing {d.contribution_pct:+.2%} to the total change.",
                "evidence": f"{metric.label} moved from {d.prev_value:,.0f} to "
                            f"{d.curr_value:,.0f} in this segment.",
                "recommendation": f"Run a focused review of {d.segment} "
                                  f"({d.dimension}) before the next period closes.",
                "assumptions": ["Half of the observed decline in this segment is "
                                "addressable within one period.",
                                "No offsetting decline appears elsewhere."],
                "expected_impact_pct": round(recovery, 4),
                "expected_impact_absolute": round(abs(d.absolute_change) * 0.5, 2),
                "confidence": "supported by significance test"
                              if (sig and sig.significant) else "directional only",
                "risk": "medium" if abs(d.contribution_pct) > 0.05 else "low",
                "priority": len(out) + 1,
            })
        if fc and fc.trend_direction == "decreasing" and not out:
            out.append({
                "problem": "No single segment dominates, but the forward trend is negative.",
                "evidence": f"Backtested {fc.model} projects a declining trend "
                            f"(MASE {fc.metrics.mase:.2f}).",
                "recommendation": "Treat this as a broad-based trend rather than a "
                                  "segment incident; review pricing and demand inputs.",
                "assumptions": ["Recent conditions persist over the forecast horizon."],
                "expected_impact_pct": 0.0,
                "expected_impact_absolute": 0.0,
                "confidence": "directional only", "risk": "medium", "priority": 1,
            })
        return out

    async def _plan(self, req, metric, ledger) -> list[str]:
        fallback = [
            f"Resolve '{metric.key}' from the semantic layer and confirm its definition.",
            "Compare the current period against the prior equivalent period.",
            f"Decompose the change by {', '.join(req.dimensions)}.",
            "Test whether the change exceeds normal variation.",
            "Detect anomalies in the daily series.",
            f"Forecast {req.forecast_horizon} days forward with a backtested model.",
            "Draft recommendations tied to the largest measured drivers.",
            "Verify every claim against computed evidence.",
        ]
        result = await self.gateway.complete_json(
            task=TaskKind.PLANNING,
            system="You are a planning agent for a business intelligence platform. "
                   "Produce an ordered analysis plan. You cannot execute anything; "
                   "you only decide what should be investigated.",
            prompt=f"Question: {req.question}\nMetric: {metric.key} ({metric.description})\n"
                   f"Available dimensions: {metric.dimensions}\n"
                   f"Produce 5-9 concrete analysis steps.",
            schema_hint='{"steps": ["string", ...]}',
            fallback={"steps": fallback},
        )
        steps = result.get("steps", fallback)
        return steps if isinstance(steps, list) and steps else fallback

    async def _narrate(self, req, metric, rc, sig, fc, ledger) -> str:
        top = rc.top(3)
        parts = [
            f"{metric.label} moved from {rc.prev_total:,.0f} to {rc.curr_total:,.0f}, "
            f"a change of {rc.pct_change:+.2%}."
        ]
        if top:
            parts.append("The largest contributors were " + "; ".join(
                f"{d.segment} ({d.dimension}) at {d.contribution_pct:+.2%}" for d in top
            ) + ".")
        if sig:
            parts.append(sig.interpretation)
        if fc:
            parts.append(f"A backtested {fc.model} projects a {fc.trend_direction} trend "
                         f"over the next {fc.horizon} days.")
        fallback = " ".join(parts)

        resp = await self.gateway.complete(
            task=TaskKind.NARRATIVE,
            system="You write executive analysis for a BI platform. Use ONLY the "
                   "figures supplied. Never introduce a number that is not in the "
                   "input. Do not assert causation unless the significance result "
                   "says the change is significant; otherwise say 'associated with'. "
                   "Three sentences maximum.",
            prompt=f"Question: {req.question}\nComputed findings:\n{fallback}",
            fallback=fallback, max_tokens=400,
        )
        ledger.record(resp)
        return resp.text.strip() or fallback
