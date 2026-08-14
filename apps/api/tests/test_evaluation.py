"""The evaluation harness must itself be trustworthy."""
import pytest

from app.evaluation.harness import THRESHOLDS, Evaluator, Scorecard


@pytest.mark.asyncio
async def test_scorecard_meets_every_threshold():
    scorecard = await Evaluator().run()
    assert scorecard.passed, scorecard.render()


@pytest.mark.asyncio
async def test_every_golden_case_passes():
    scorecard = await Evaluator().run()
    failed = [(c.case_id, c.failures) for c in scorecard.cases if not c.passed]
    assert not failed, failed


def test_a_breached_threshold_fails_the_run():
    """A scorecard that cannot fail measures nothing."""
    card = Scorecard(metrics={"sql_block_rate": 0.5}, cases=[])
    assert not card.passed
    assert "sql_block_rate" in card.breaches[0]


def test_grounding_catches_every_fabricated_number():
    rate, clean_approved = Evaluator().grounding_catch_rate()
    assert rate == 1.0
    assert clean_approved == 1, "a truthful narrative must not be rejected"


def test_adversarial_sql_is_fully_blocked():
    assert Evaluator().sql_block_rate() == 1.0


def test_thresholds_are_non_trivial():
    """Floors of zero would make the suite decorative."""
    assert all(v > 0 for v in THRESHOLDS.values())
