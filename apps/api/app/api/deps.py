"""Request dependencies: authentication, tenant resolution, permission gates."""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.core.security import Permission, Role, decode_token, role_has

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    role: Role

    def can(self, permission: Permission) -> bool:
        return role_has(self.role, permission)


async def current_principal(authorization: str = Header(default="")) -> Principal:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    try:
        claims = decode_token(authorization.split(" ", 1)[1])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    if claims.workspace_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Select a workspace first")
    return Principal(claims.user_id, claims.workspace_id, claims.role)


def requires(permission: Permission):
    """Route guard. Denial is logged to the audit trail by the router."""
    async def _guard(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role '{principal.role}' lacks '{permission}'.",
            )
        return principal
    return _guard


# --- Database ---------------------------------------------------------------

async def db_session() -> AsyncIterator["AsyncSession"]:
    """Unscoped session. Only for endpoints that run before a workspace is
    known -- signup, login, refresh. Everything else uses ``tenant_session``."""
    from app.db.session import session_scope
    async with session_scope() as session:
        yield session


async def tenant_session(
    principal: Principal = Depends(current_principal),
) -> AsyncIterator["AsyncSession"]:
    """Session with the caller's workspace filter attached to every SELECT.

    Handlers do not add ``WHERE workspace_id = ...`` themselves; forgetting it
    is the single most common multi-tenant data leak, so the filter is applied
    by the session rather than trusted to each route.
    """
    from app.db.session import session_scope
    async with session_scope(principal.workspace_id) as session:
        yield session
