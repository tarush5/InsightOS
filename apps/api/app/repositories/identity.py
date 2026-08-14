"""Users, workspaces, memberships and refresh-token revocation."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Role, hash_password, verify_password
from app.db.models import Membership, RefreshToken, User, Workspace


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:70] or "workspace"


class EmailAlreadyRegistered(Exception):
    pass


class IdentityRepository:
    """All identity writes go through here so hashing and revocation stay in one place."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- registration ----------------------------------------------------
    async def signup(self, *, email: str, password: str, full_name: str,
                     workspace_name: str) -> tuple[User, Workspace, Membership]:
        email = email.strip().lower()
        existing = await self.session.scalar(select(User).where(User.email == email))
        if existing is not None:
            raise EmailAlreadyRegistered(email)

        user = User(email=email, full_name=full_name, password_hash=hash_password(password))
        base = slugify(workspace_name)
        slug, n = base, 1
        while await self.session.scalar(select(Workspace).where(Workspace.slug == slug)):
            n += 1
            slug = f"{base}-{n}"
        workspace = Workspace(name=workspace_name, slug=slug)
        self.session.add_all([user, workspace])
        await self.session.flush()

        # The first member of a new workspace owns it.
        membership = Membership(user_id=user.id, workspace_id=workspace.id, role=Role.ADMIN)
        self.session.add(membership)
        await self.session.flush()
        return user, workspace, membership

    # --- authentication --------------------------------------------------
    async def authenticate(self, email: str, password: str) -> tuple[User, Membership] | None:
        """Returns None for both unknown-user and bad-password; the caller must
        not distinguish them. A dummy verify runs on the unknown-user path so
        response time does not reveal whether the account exists."""
        user = await self.session.scalar(
            select(User).where(User.email == email.strip().lower()))
        if user is None or not user.is_active:
            verify_password(password, hash_password("timing-equalisation-dummy"))
            return None
        if not verify_password(password, user.password_hash):
            return None
        membership = await self.session.scalar(
            select(Membership).where(Membership.user_id == user.id)
            .order_by(Membership.created_at))
        if membership is None:
            return None
        user.last_login_at = datetime.now(timezone.utc)
        return user, membership

    async def membership_for(self, user_id: uuid.UUID,
                             workspace_id: uuid.UUID) -> Membership | None:
        return await self.session.scalar(
            select(Membership).where(Membership.user_id == user_id,
                                     Membership.workspace_id == workspace_id))

    # --- refresh-token lifecycle ----------------------------------------
    async def record_refresh(self, user_id: uuid.UUID, jti: str, ttl_seconds: int) -> None:
        self.session.add(RefreshToken(
            user_id=user_id, jti=jti,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)))

    async def is_refresh_active(self, jti: str) -> bool:
        row = await self.session.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
        if row is None or row.revoked_at is not None:
            return False
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > datetime.now(timezone.utc)

    async def revoke_refresh(self, jti: str) -> None:
        await self.session.execute(
            update(RefreshToken).where(RefreshToken.jti == jti,
                                       RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc)))

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(RefreshToken).where(RefreshToken.user_id == user_id,
                                       RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc)))
        return int(result.rowcount or 0)
