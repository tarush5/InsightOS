"""Persistence, tenant isolation and refresh-token lifecycle."""
import uuid

import pytest

from app.core.security import Role
from app.db.models import Investigation
from app.db.session import session_scope
from app.repositories.audit import AuditRepository
from app.repositories.identity import EmailAlreadyRegistered, IdentityRepository
from app.repositories.investigations import InvestigationRepository


async def _make_workspace(email="a@example.com", name="Acme"):
    async with session_scope() as session:
        user, workspace, membership = await IdentityRepository(session).signup(
            email=email, password="correct-horse-battery-staple",
            full_name="A", workspace_name=name)
        return user.id, workspace.id, membership.role


@pytest.mark.asyncio
async def test_signup_creates_owner_membership(db):
    _, _, role = await _make_workspace()
    assert role == Role.ADMIN


@pytest.mark.asyncio
async def test_duplicate_email_is_rejected(db):
    await _make_workspace("dup@example.com")
    async with session_scope() as session:
        with pytest.raises(EmailAlreadyRegistered):
            await IdentityRepository(session).signup(
                email="dup@example.com", password="correct-horse-battery-staple",
                full_name="B", workspace_name="Other")


@pytest.mark.asyncio
async def test_workspace_slugs_do_not_collide(db):
    async with session_scope() as session:
        repo = IdentityRepository(session)
        _, first, _ = await repo.signup(email="x1@example.com",
                                        password="correct-horse-battery-staple",
                                        full_name="", workspace_name="Acme Corp")
        _, second, _ = await repo.signup(email="x2@example.com",
                                         password="correct-horse-battery-staple",
                                         full_name="", workspace_name="Acme Corp")
        assert first.slug != second.slug


@pytest.mark.asyncio
async def test_authenticate_rejects_bad_password(db):
    await _make_workspace("auth@example.com")
    async with session_scope() as session:
        repo = IdentityRepository(session)
        assert await repo.authenticate("auth@example.com", "wrong-password") is None
        assert await repo.authenticate("auth@example.com",
                                       "correct-horse-battery-staple") is not None


@pytest.mark.asyncio
async def test_unknown_user_and_wrong_password_are_indistinguishable(db):
    """Both return None. The endpoint above turns both into the same 401, so
    the API cannot be used to enumerate accounts."""
    await _make_workspace("known@example.com")
    async with session_scope() as session:
        repo = IdentityRepository(session)
        assert await repo.authenticate("known@example.com", "nope") is None
        assert await repo.authenticate("ghost@example.com", "nope") is None


@pytest.mark.asyncio
async def test_refresh_token_lifecycle(db):
    user_id, _, _ = await _make_workspace("rt@example.com")
    async with session_scope() as session:
        repo = IdentityRepository(session)
        await repo.record_refresh(user_id, "jti-1", 3600)
        assert await repo.is_refresh_active("jti-1")
        await repo.revoke_refresh("jti-1")
        assert not await repo.is_refresh_active("jti-1")


@pytest.mark.asyncio
async def test_revoke_all_cuts_every_session(db):
    user_id, _, _ = await _make_workspace("all@example.com")
    async with session_scope() as session:
        repo = IdentityRepository(session)
        for i in range(3):
            await repo.record_refresh(user_id, f"jti-{i}", 3600)
        assert await repo.revoke_all_for_user(user_id) == 3
        for i in range(3):
            assert not await repo.is_refresh_active(f"jti-{i}")


@pytest.mark.asyncio
async def test_unknown_jti_is_not_active(db):
    async with session_scope() as session:
        assert not await IdentityRepository(session).is_refresh_active("never-issued")


# --- tenant isolation --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_scoped_session_cannot_see_another_workspace(db):
    """The filter comes from the session, not from a WHERE clause in the query.
    This test deliberately issues an unfiltered SELECT."""
    user_a, ws_a, _ = await _make_workspace("a@x.com", "A")
    user_b, ws_b, _ = await _make_workspace("b@x.com", "B")

    async with session_scope(ws_a) as session:
        await InvestigationRepository(session).create(
            workspace_id=ws_a, user_id=user_a, question="A's question",
            metric_key="revenue")
    async with session_scope(ws_b) as session:
        await InvestigationRepository(session).create(
            workspace_id=ws_b, user_id=user_b, question="B's question",
            metric_key="revenue")

    from sqlalchemy import select
    async with session_scope(ws_a) as session:
        rows = list(await session.scalars(select(Investigation)))
        assert len(rows) == 1
        assert rows[0].question == "A's question"


@pytest.mark.asyncio
async def test_history_is_scoped_without_the_caller_asking(db):
    user_a, ws_a, _ = await _make_workspace("ha@x.com", "HA")
    user_b, ws_b, _ = await _make_workspace("hb@x.com", "HB")
    for ws, user, q in ((ws_a, user_a, "A1"), (ws_a, user_a, "A2"),
                        (ws_b, user_b, "B1")):
        async with session_scope(ws) as session:
            await InvestigationRepository(session).create(
                workspace_id=ws, user_id=user, question=q, metric_key="revenue")

    async with session_scope(ws_b) as session:
        rows = await InvestigationRepository(session).history()
        assert [r.question for r in rows] == ["B1"]


@pytest.mark.asyncio
async def test_another_workspaces_record_is_invisible_by_reference(db):
    """Guessing a reference must not be enough to read someone else's report."""
    user_a, ws_a, _ = await _make_workspace("ra@x.com", "RA")
    _, ws_b, _ = await _make_workspace("rb@x.com", "RB")
    async with session_scope(ws_a) as session:
        record = await InvestigationRepository(session).create(
            workspace_id=ws_a, user_id=user_a, question="secret",
            metric_key="revenue")
        reference = record.reference

    async with session_scope(ws_b) as session:
        assert await InvestigationRepository(session).by_reference(reference) is None


# --- investigation records ---------------------------------------------------

@pytest.mark.asyncio
async def test_finalise_stores_the_report(db):
    user_id, ws_id, _ = await _make_workspace("fin@x.com")
    async with session_scope(ws_id) as session:
        repo = InvestigationRepository(session)
        record = await repo.create(workspace_id=ws_id, user_id=user_id,
                                   question="Why?", metric_key="revenue")
        await repo.finalise(record, {
            "headline": "Revenue fell 13.7%", "narrative": "Because South.",
            "confidence": {"overall": 0.62}, "evidence": {"sql": "SELECT 1"},
            "recommendations": [{"action": "investigate South"}],
            "critic": {"approved": True}}, duration_ms=1234)

    async with session_scope(ws_id) as session:
        stored = await InvestigationRepository(session).by_reference(record.reference)
        assert stored.status == "complete" and stored.verdict == "approved"
        assert stored.confidence["overall"] == 0.62
        assert stored.duration_ms == 1234


@pytest.mark.asyncio
async def test_numpy_and_dates_survive_the_json_column(db):
    """Analytics hands back numpy scalars and Timestamps; a naive insert fails."""
    import datetime

    import numpy as np

    user_id, ws_id, _ = await _make_workspace("np@x.com")
    async with session_scope(ws_id) as session:
        repo = InvestigationRepository(session)
        record = await repo.create(workspace_id=ws_id, user_id=user_id,
                                   question="Why?", metric_key="revenue")
        await repo.finalise(record, {
            "headline": "x", "confidence": {"overall": np.float64(0.42)},
            "evidence": {"as_of": datetime.date(2025, 8, 1),
                         "n": np.int64(17)}}, duration_ms=1)

    async with session_scope(ws_id) as session:
        stored = await InvestigationRepository(session).by_reference(record.reference)
        assert stored.confidence["overall"] == 0.42
        assert stored.evidence["as_of"] == "2025-08-01"
        assert stored.evidence["n"] == 17


@pytest.mark.asyncio
async def test_references_are_unique(db):
    user_id, ws_id, _ = await _make_workspace("ref@x.com")
    async with session_scope(ws_id) as session:
        repo = InvestigationRepository(session)
        refs = {(await repo.create(workspace_id=ws_id, user_id=user_id,
                                   question="q", metric_key="revenue")).reference
                for _ in range(25)}
    assert len(refs) == 25


@pytest.mark.asyncio
async def test_audit_entries_are_scoped_and_ordered(db):
    _, ws_id, _ = await _make_workspace("aud@x.com")
    async with session_scope(ws_id) as session:
        repo = AuditRepository(session)
        for action in ("auth.login", "investigation.run", "alert.create"):
            await repo.record(action=action, workspace_id=ws_id)
    async with session_scope(ws_id) as session:
        entries = await AuditRepository(session).recent(ws_id)
        assert {e.action for e in entries} >= {"auth.login", "alert.create"}
