"""Password hashing, JWT issue/verify, and RBAC primitives."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum

import jwt

from app.core.config import settings

# OWASP's 2023 floor for PBKDF2-HMAC-SHA256. Overridable only downwards for
# tests, where a full-strength hash on every fixture makes the suite slow enough
# that people stop running it. The stored hash records the round count it used,
# so a value lowered here never weakens hashes written at the production
# setting -- verification reads the count back out of the encoded string.
_PBKDF2_ROUNDS = settings.PASSWORD_HASH_ROUNDS


class Role(StrEnum):
    ADMIN = "admin"
    DATA_SCIENTIST = "data_scientist"
    ANALYST = "analyst"
    VIEWER = "viewer"


class Permission(StrEnum):
    WORKSPACE_MANAGE = "workspace:manage"
    MEMBER_MANAGE = "member:manage"
    DATASOURCE_WRITE = "datasource:write"
    DATASOURCE_READ = "datasource:read"
    SQL_EXECUTE = "sql:execute"
    SQL_RAW_EXECUTE = "sql:raw_execute"
    METRIC_WRITE = "metric:write"
    METRIC_APPROVE = "metric:approve"
    MODEL_TRAIN = "model:train"
    INVESTIGATION_RUN = "investigation:run"
    INVESTIGATION_READ = "investigation:read"
    REPORT_EXPORT = "report:export"
    AUDIT_READ = "audit:read"
    ALERT_MANAGE = "alert:manage"
    SIMULATION_RUN = "simulation:run"


_VIEWER = {Permission.DATASOURCE_READ, Permission.INVESTIGATION_READ}
_ANALYST = _VIEWER | {
    Permission.SQL_EXECUTE,
    Permission.INVESTIGATION_RUN,
    Permission.METRIC_WRITE,
    Permission.REPORT_EXPORT,
    Permission.ALERT_MANAGE,
    Permission.SIMULATION_RUN,
}
_DATA_SCIENTIST = _ANALYST | {Permission.MODEL_TRAIN, Permission.SQL_RAW_EXECUTE}
_ADMIN = set(Permission)

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: _VIEWER,
    Role.ANALYST: _ANALYST,
    Role.DATA_SCIENTIST: _DATA_SCIENTIST,
    Role.ADMIN: _ADMIN,
}


def role_has(role: Role | str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(Role(role), set())


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


WS_TICKET_TTL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    workspace_id: uuid.UUID | None
    role: Role
    token_type: str
    jti: str


def issue_token(
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    role: Role,
    token_type: str = "access",
    ttl_seconds: int | None = None,
) -> tuple[str, str]:
    """Returns (token, jti). The jti is persisted so tokens can be revoked.

    ``ws`` tickets are deliberately short-lived: a WebSocket URL ends up in
    proxy logs and browser history, so the credential in it must be worthless
    within about a minute of being issued.
    """
    ttl = ttl_seconds if ttl_seconds is not None else {
        "access": settings.ACCESS_TOKEN_TTL_SECONDS,
        "ws": WS_TICKET_TTL_SECONDS,
    }.get(token_type, settings.REFRESH_TOKEN_TTL_SECONDS)
    jti = secrets.token_urlsafe(24)
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "ws": str(workspace_id) if workspace_id else None,
        "role": str(role),
        "typ": token_type,
        "jti": jti,
        "iat": now,
        "exp": now + ttl,
        "iss": "insightos",
    }
    return jwt.encode(payload, settings.AUTH_SECRET, algorithm="HS256"), jti


def decode_token(token: str, *, expected_type: str = "access") -> TokenClaims:
    payload = jwt.decode(token, settings.AUTH_SECRET, algorithms=["HS256"], issuer="insightos")
    if payload.get("typ") != expected_type:
        raise jwt.InvalidTokenError(f"Expected {expected_type} token")
    return TokenClaims(
        user_id=uuid.UUID(payload["sub"]),
        workspace_id=uuid.UUID(payload["ws"]) if payload.get("ws") else None,
        role=Role(payload["role"]),
        token_type=payload["typ"],
        jti=payload["jti"],
    )
