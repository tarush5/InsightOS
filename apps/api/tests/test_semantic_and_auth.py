import pytest

from app.core.security import (Permission, Role, decode_token, hash_password,
                               issue_token, role_has, verify_password)
from app.semantic.registry import (Aggregation, MetricDefinition, MetricRegistry,
                                   MetricStatus, default_registry)


def test_password_roundtrip():
    h = hash_password("correct-horse-battery")
    assert verify_password("correct-horse-battery", h)
    assert not verify_password("wrong-horse-battery", h)


def test_short_passwords_rejected():
    with pytest.raises(ValueError):
        hash_password("short")


def test_password_hash_is_salted():
    assert hash_password("correct-horse-battery") != hash_password("correct-horse-battery")


def test_rbac_matrix():
    assert role_has(Role.VIEWER, Permission.INVESTIGATION_READ)
    assert not role_has(Role.VIEWER, Permission.SQL_EXECUTE)
    assert not role_has(Role.ANALYST, Permission.MODEL_TRAIN)
    assert role_has(Role.DATA_SCIENTIST, Permission.MODEL_TRAIN)
    assert role_has(Role.ADMIN, Permission.AUDIT_READ)
    assert not role_has(Role.ANALYST, Permission.WORKSPACE_MANAGE)


def test_token_roundtrip_carries_tenant():
    import uuid
    uid, wid = uuid.uuid4(), uuid.uuid4()
    token, jti = issue_token(user_id=uid, workspace_id=wid, role=Role.ANALYST)
    claims = decode_token(token)
    assert claims.user_id == uid and claims.workspace_id == wid
    assert claims.role is Role.ANALYST and claims.jti == jti


def test_access_token_rejected_where_refresh_expected():
    import uuid
    import jwt
    token, _ = issue_token(user_id=uuid.uuid4(), workspace_id=None, role=Role.VIEWER)
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, expected_type="refresh")


def test_unapproved_metric_cannot_be_used():
    r = MetricRegistry()
    r.register(MetricDefinition(
        key="sketchy", label="Sketchy", description="draft",
        aggregation=Aggregation.SUM, expression="SUM(x)",
        base_table="orders", date_column="d"))
    with pytest.raises(PermissionError):
        r.require_approved("sketchy")
    r.approve("sketchy", "owner@example.com")
    assert r.require_approved("sketchy").status is MetricStatus.APPROVED


def test_metric_expression_cannot_contain_writes():
    r = MetricRegistry()
    with pytest.raises(ValueError):
        r.register(MetricDefinition(
            key="evil", label="Evil", description="",
            aggregation=Aggregation.SUM, expression="SUM(x); DROP TABLE orders",
            base_table="orders", date_column="d"))


def test_unknown_metric_raises_with_available_list():
    with pytest.raises(KeyError, match="revenue"):
        default_registry().require_approved("profit_margin_v9")


def test_metric_search_grounds_question_in_real_metrics():
    hits = default_registry().search("why did revenue drop last month")
    assert hits and hits[0].key == "revenue"
