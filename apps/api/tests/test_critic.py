"""The critic is the last line of defence against a fluent, wrong answer."""
from app.agents.critic import CriticAgent


def _evidence():
    return {"sql": "SELECT 1", "metric": {"key": "revenue"},
            "root_cause": {"pct_change": -0.138,
                           "drivers": [{"segment": "South", "contribution_pct": -0.284}]}}


def test_hallucinated_number_is_caught():
    report = CriticAgent().review(
        narrative="Revenue fell 42.7% driven by the South region.",
        evidence=_evidence(), sql_validated=True, reconciliation_error=0.0,
        significance={"significant": True, "p_value": 0.001},
        forecast_metrics=None, row_count=100,
    )
    assert not report.approved
    check = next(c for c in report.checks if c.name == "numbers_grounded")
    assert not check.passed


def test_grounded_numbers_pass():
    report = CriticAgent().review(
        narrative="Revenue fell 13.8%, with South contributing -28.4%.",
        evidence=_evidence(), sql_validated=True, reconciliation_error=0.0,
        significance={"significant": True, "p_value": 0.001},
        forecast_metrics=None, row_count=100,
    )
    assert report.approved, [c.detail for c in report.checks if not c.passed]


def test_causal_language_without_significance_is_blocked():
    report = CriticAgent().review(
        narrative="Revenue fell 13.8% because of the South region.",
        evidence=_evidence(), sql_validated=True, reconciliation_error=0.0,
        significance={"significant": False, "p_value": 0.42},
        forecast_metrics=None, row_count=100,
    )
    assert not report.approved
    assert not next(c for c in report.checks
                    if c.name == "causal_claims_supported").passed


def test_empty_result_blocks_conclusion():
    report = CriticAgent().review(
        narrative="Revenue fell 13.8%.", evidence=_evidence(), sql_validated=True,
        reconciliation_error=0.0, significance=None, forecast_metrics=None, row_count=0,
    )
    assert not report.approved


def test_broken_attribution_blocks_conclusion():
    report = CriticAgent().review(
        narrative="Revenue fell 13.8%.", evidence=_evidence(), sql_validated=True,
        reconciliation_error=0.05, significance=None, forecast_metrics=None, row_count=10,
    )
    assert not report.approved
    assert not next(c for c in report.checks
                    if c.name == "attribution_reconciles").passed


def test_unvalidated_sql_blocks_conclusion():
    report = CriticAgent().review(
        narrative="Revenue fell 13.8%.", evidence=_evidence(), sql_validated=False,
        reconciliation_error=0.0, significance=None, forecast_metrics=None, row_count=10,
    )
    assert not report.approved
