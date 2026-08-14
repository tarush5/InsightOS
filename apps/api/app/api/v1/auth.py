"""
Authentication routes (spec section 8).

Users, workspaces and refresh tokens are persisted. Refresh tokens rotate on
every use: the presented token is revoked as its replacement is issued, so a
stolen token is usable at most once. Reuse of an already-rotated token is
treated as compromise -- every session for that user is revoked, not just the
one presented, because there is no way to tell the victim's request from the
attacker's.
"""
from __future__ import annotations

import uuid

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, current_principal, db_session
from app.core.config import settings
from app.core.security import ROLE_PERMISSIONS, Role, decode_token, issue_token
from app.repositories.audit import AuditRepository
from app.repositories.identity import EmailAlreadyRegistered, IdentityRepository

router = APIRouter()

INVALID_CREDENTIALS = "Invalid email or password"


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(default="", max_length=160)
    workspace_name: str = Field(min_length=2, max_length=160)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    workspace_id: str
    role: str


class RefreshRequest(BaseModel):
    refresh_token: str


async def _issue_pair(repo: IdentityRepository, user_id: uuid.UUID,
                      workspace_id: uuid.UUID, role: Role) -> TokenPair:
    access, _ = issue_token(user_id=user_id, workspace_id=workspace_id, role=role)
    refresh, jti = issue_token(user_id=user_id, workspace_id=workspace_id,
                               role=role, token_type="refresh")
    await repo.record_refresh(user_id, jti, settings.REFRESH_TOKEN_TTL_SECONDS)
    return TokenPair(access_token=access, refresh_token=refresh,
                     expires_in=settings.ACCESS_TOKEN_TTL_SECONDS,
                     workspace_id=str(workspace_id), role=str(role))


@router.post("/signup", response_model=TokenPair, status_code=201)
async def signup(payload: SignupRequest,
                 session: AsyncSession = Depends(db_session)) -> TokenPair:
    repo = IdentityRepository(session)
    try:
        user, workspace, membership = await repo.signup(
            email=payload.email, password=payload.password,
            full_name=payload.full_name, workspace_name=payload.workspace_name)
    except EmailAlreadyRegistered:
        # Signup cannot be made non-enumerable without an email round-trip,
        # which this endpoint does not do. Login stays constant-response, and
        # that is where guessing actually happens.
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "An account with that email already exists.")
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    await AuditRepository(session).record(
        action="auth.signup", workspace_id=workspace.id, user_id=user.id,
        resource_type="workspace", resource_id=str(workspace.id))
    return await _issue_pair(repo, user.id, workspace.id, Role(membership.role))


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest,
                session: AsyncSession = Depends(db_session)) -> TokenPair:
    repo = IdentityRepository(session)
    found = await repo.authenticate(payload.email, payload.password)
    if found is None:
        await AuditRepository(session).record(
            action="auth.login_failed", resource_type="email",
            resource_id=payload.email[:96])
        # Committed before raising: the session rolls back on exception, which
        # would otherwise discard the record of every failed attempt -- exactly
        # the ones worth keeping.
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)

    user, membership = found
    await AuditRepository(session).record(
        action="auth.login", workspace_id=membership.workspace_id, user_id=user.id,
        resource_type="user", resource_id=str(user.id))
    return await _issue_pair(repo, user.id, membership.workspace_id,
                             Role(membership.role))


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest,
                  session: AsyncSession = Depends(db_session)) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    repo = IdentityRepository(session)
    if not await repo.is_refresh_active(claims.jti):
        revoked = await repo.revoke_all_for_user(claims.user_id)
        await AuditRepository(session).record(
            action="auth.refresh_reuse_detected", user_id=claims.user_id,
            workspace_id=claims.workspace_id, resource_type="sessions",
            resource_id=str(revoked))
        # The revocation must survive the exception that reports it. Without
        # this commit the rollback undoes the response to the compromise, and
        # the stolen token keeps working -- the failure is silent, because the
        # caller still sees a 401 and assumes the sessions were cut.
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Refresh token is no longer valid. Please sign in again.")

    await repo.revoke_refresh(claims.jti)
    if claims.workspace_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token carries no workspace")
    membership = await repo.membership_for(claims.user_id, claims.workspace_id)
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "You no longer have access to this workspace.")
    return await _issue_pair(repo, claims.user_id, claims.workspace_id,
                             Role(membership.role))


@router.post("/logout", status_code=204, response_class=Response)
async def logout(payload: RefreshRequest,
                 principal: Principal = Depends(current_principal),
                 session: AsyncSession = Depends(db_session)) -> Response:
    try:
        claims = decode_token(payload.refresh_token, expected_type="refresh")
    except jwt.InvalidTokenError:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if claims.user_id != principal.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "That token belongs to another session.")
    await IdentityRepository(session).revoke_refresh(claims.jti)
    await AuditRepository(session).record(
        action="auth.logout", workspace_id=principal.workspace_id,
        user_id=principal.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
async def me(principal: Principal = Depends(current_principal)) -> dict:
    return {
        "user_id": str(principal.user_id),
        "workspace_id": str(principal.workspace_id),
        "role": str(principal.role),
        "permissions": sorted(str(p) for p in ROLE_PERMISSIONS.get(principal.role, ())),
    }
