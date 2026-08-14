"""End-to-end: the orchestrator must complete and stay internally consistent."""
import numpy as np
import pandas as pd
import pytest

from app.agents.orchestrator import (InvestigationOrchestrator, InvestigationRequest,
                                     Stage, StageState)
from app.semantic.registry import default_registry


@pytest.fixture
def provider():
    rng = np.random.default_rng(7)
    rows = []
    for period_start, factor in (("2025-07-01", 1.0), ("2025-08-01", 1.0)):
        for day in range(31):
            for region in ("North", "South", "East"):
                hit = 0.55 if (region == "South" and period_start == "2025-08-01") else 1.0
                rows.append({
                    "order_date": pd.Timestamp(period_start) + pd.Timedelta(days=day),
                    "region": region, "segment": "Enterprise", "channel": "Web",
                    "revenue": float(rng.normal(10_000, 400) * factor * hit),
                })
    frame = pd.DataFrame(rows)

    def _provider(metric, start, end):
        mask = ((frame.order_date >= pd.Timestamp(start)) &
                (frame.order_date <= pd.Timestamp(end)))
        return frame.loc[mask].copy()
    return _provider


async def _run(provider, **kw):
    from datetime import date
    orch = InvestigationOrchestrator(registry=default_registry(), data_provider=provider)
    req = InvestigationRequest(
        question="Why did revenue decrease in August?", metric_key="revenue",
        current_period=(date(2025, 8, 1), date(2025, 8, 31)),
        comparison_period=(date(2025, 7, 1), date(2025, 7, 31)),
        dimensions=["region", "segment", "channel"], **kw,
    )
    return [e async for e in orch.run(req)]


@pytest.mark.asyncio
async def test_investigation_completes(provider):
    events = await _run(provider)
    final = events[-1]
    assert final.stage is Stage.COMPLETE and final.state is StageState.DONE


@pytest.mark.asyncio
async def test_progress_is_monotonic(provider):
    """A progress bar that goes backwards is a bug users will notice immediately."""
    done = [e.progress for e in await _run(provider) if e.state is not StageState.RUNNING]
    assert done == sorted(done), done


@pytest.mark.asyncio
async def test_engineered_driver_is_found_without_being_told(provider):
    final = (await _run(provider))[-1]
    top = final.detail["drivers"][0]
    assert top["segment"] == "South"
    assert top["contribution_pct"] < 0


@pytest.mark.asyncio
async def test_critic_approves_a_grounded_narrative(provider):
    """With no LLM configured the narrative is templated from computed values,
    so every number must be groundable by construction."""
    final = (await _run(provider))[-1]
    assert final.detail["critic"]["approved"], final.detail["critic"]["checks"]


@pytest.mark.asyncio
async def test_confidence_names_a_limiting_factor(provider):
    conf = (await _run(provider))[-1].detail["confidence"]
    assert conf["limiting_factor"] in {"data", "statistical", "model", "reasoning"}
    assert 0 < conf["overall"] <= 1


@pytest.mark.asyncio
async def test_evidence_is_attached_to_every_conclusion(provider):
    evidence = (await _run(provider))[-1].detail["evidence"]
    for key in ("sql", "metric", "root_cause", "profile"):
        assert key in evidence, f"missing evidence: {key}"


@pytest.mark.asyncio
async def test_unknown_metric_fails_before_touching_data(provider):
    from datetime import date
    orch = InvestigationOrchestrator(registry=default_registry(), data_provider=provider)
    req = InvestigationRequest(question="What is our unicorn index?",
                               metric_key="unicorn_index",
                               current_period=(date(2025, 8, 1), date(2025, 8, 31)),
                               comparison_period=(date(2025, 7, 1), date(2025, 7, 31)))
    events = [e async for e in orch.run(req)]
    assert events[-1].state is StageState.FAILED
    assert events[-1].stage is Stage.RESOLVE_METRIC
