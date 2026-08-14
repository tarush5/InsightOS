"""Export: qualifiers must travel with every figure."""
import pytest

from app.reports.export import build_markdown, build_report, to_pdf

LOW_CONFIDENCE = {
    "reference": "INV-ABC123",
    "question": "Why did revenue decrease in August?",
    "headline": "Revenue increased 1.0% versus the comparison period.",
    "narrative": "Aggregate revenue was flat while South fell sharply.",
    "verdict": "approved",
    "confidence": {"overall": 0.27, "data": 0.75, "statistical": 0.04,
                   "model": 0.63, "reasoning": 1.0,
                   "limiting_factor": "statistical"},
    "recommendations": [
        {"action": "Investigate South support staffing",
         "rationale": "First response time tripled there.",
         "expected_impact": "320,000"}],
    "evidence": {
        "sql": "SELECT region, SUM(total_amount) FROM orders GROUP BY region",
        "metric": {"current": 812450.0, "previous": 941200.0, "change_pct": -0.1368},
        "significance": {"p_value": 0.8991, "significant": False},
        "root_cause": {"drivers": [
            {"dimension": "region", "segment": "South",
             "contribution_pct": -0.1501, "change_pct": -0.192},
            {"dimension": "region", "segment": "West",
             "contribution_pct": 0.0733, "change_pct": 0.081}]},
        "causal": {"credible": False,
                   "diagnostics": [{"name": "parallel_trends", "passed": False}],
                   "interpretation": "irrelevant when withheld"},
    },
}


def test_a_low_confidence_score_is_never_shown_bare():
    """A confidence figure without its limiting factor invites the reader to
    treat 27% as a minor caveat rather than as the finding."""
    markdown = build_markdown(LOW_CONFIDENCE)
    assert "27%" in markdown
    assert "statistical" in markdown
    assert "Directional only" in markdown


def test_the_limiting_factor_is_explained_not_just_named():
    markdown = build_markdown(LOW_CONFIDENCE)
    assert "within what normal variation produces" in markdown


def test_drivers_carry_both_contribution_and_own_change():
    """A large percentage on a small segment moves the total very little, and a
    report showing only one of the two numbers hides which case it is."""
    markdown = build_markdown(LOW_CONFIDENCE)
    assert "-15.01%" in markdown and "-19.2%" in markdown
    assert "sums" in markdown


def test_an_uncredible_causal_estimate_is_withheld_not_footnoted():
    markdown = build_markdown(LOW_CONFIDENCE)
    assert "Withheld" in markdown
    assert "parallel_trends" in markdown
    assert "irrelevant when withheld" not in markdown


def test_expected_impact_always_states_its_basis():
    """Without it the figure reads as a forecast rather than arithmetic on an
    assumed recovery rate."""
    markdown = build_markdown(LOW_CONFIDENCE)
    assert "320,000" in markdown
    assert "recovers halfway" in markdown


def test_a_supplied_impact_basis_is_used():
    payload = dict(LOW_CONFIDENCE)
    payload["recommendations"] = [{"action": "Do the thing",
                                   "expected_impact": "10,000",
                                   "impact_basis": "measured in the 2024 pilot"}]
    assert "measured in the 2024 pilot" in build_markdown(payload)


def test_the_query_is_included_so_figures_can_be_checked():
    assert "SELECT region" in build_markdown(LOW_CONFIDENCE)


def test_a_flagged_verdict_warns_prominently():
    payload = dict(LOW_CONFIDENCE, verdict="flagged")
    markdown = build_markdown(payload)
    assert "flagged by the verification step" in markdown
    assert "unverified" in markdown


def test_a_high_confidence_report_is_not_hedged():
    payload = dict(LOW_CONFIDENCE,
                   confidence={"overall": 0.86, "limiting_factor": "model"})
    markdown = build_markdown(payload)
    assert "Directional only" not in markdown
    assert "high" in markdown


def test_a_sparse_investigation_still_renders():
    """A failed or partial run must still export rather than raising."""
    markdown = build_markdown({"reference": "INV-X", "question": "What happened?"})
    assert "What happened?" in markdown and "INV-X" in markdown


def test_report_filename_uses_the_reference():
    assert build_report(LOW_CONFIDENCE).filename == "INV-ABC123.md"


# --- PDF ---------------------------------------------------------------------

def test_pdf_renders():
    pdf = to_pdf(build_markdown(LOW_CONFIDENCE))
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1500


def test_pdf_keeps_tables_rather_than_dropping_them():
    """A missing table in an exported report is worse than an ugly one."""
    small = to_pdf("# T\n\ntext only\n")
    with_table = to_pdf("# T\n\ntext only\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    assert len(with_table) > len(small)


def test_pdf_survives_markup_that_would_break_the_renderer():
    pdf = to_pdf("# A <b>title</b> & more\n\nBody with <unclosed and & ampersand\n")
    assert pdf.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_export_endpoint_returns_markdown_and_pdf(client):
    import uuid as _uuid

    from app.db.session import session_scope
    from app.repositories.identity import IdentityRepository
    from app.repositories.investigations import InvestigationRepository

    response = await client.post("/api/v1/auth/signup", json={
        "email": f"exp-{_uuid.uuid4().hex[:6]}@example.com",
        "password": "correct-horse-battery-staple", "full_name": "",
        "workspace_name": "Export"})
    tokens = response.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    workspace_id = _uuid.UUID(tokens["workspace_id"])

    async with session_scope(workspace_id) as session:
        user_id = (await IdentityRepository(session).authenticate(
            response.request.read().decode().split('"email":"')[1].split('"')[0],
            "correct-horse-battery-staple"))[0].id
        repo = InvestigationRepository(session)
        record = await repo.create(workspace_id=workspace_id, user_id=user_id,
                                   question="Why did revenue fall?",
                                   metric_key="revenue")
        await repo.finalise(record, {
            "headline": "Revenue fell 13.7%",
            "confidence": {"overall": 0.27, "limiting_factor": "statistical"},
            "evidence": {"sql": "SELECT 1"}, "critic": {"approved": True}},
            duration_ms=10)
        reference = record.reference

    markdown = await client.get(
        f"/api/v1/investigations/{reference}/export", headers=headers)
    assert markdown.status_code == 200
    assert "Directional only" in markdown.text
    assert "attachment" in markdown.headers["content-disposition"]

    pdf = await client.get(
        f"/api/v1/investigations/{reference}/export?format=pdf", headers=headers)
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_export_of_an_unknown_reference_is_404(client, registered):
    response = await client.get("/api/v1/investigations/INV-NOPE99/export",
                                headers=registered["headers"])
    assert response.status_code == 404
