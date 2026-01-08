from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import MagicLinkToken, User
from shared.utils import utc_now


async def create_magic_token(
    session: AsyncSession,
    *,
    user_id: int,
    ttl_minutes: int = 15,
    scope: str | None = None,
) -> str:
    token = uuid4().hex
    expires_at = utc_now() + timedelta(minutes=int(ttl_minutes))
    row = MagicLinkToken(
        token=str(token),
        user_id=int(user_id),
        expires_at=expires_at,
        used_at=None,
        scope=(str(scope) if scope is not None else None),
    )
    session.add(row)
    await session.flush()
    return str(token)


async def validate_magic_token(
    session: AsyncSession,
    *,
    token: str,
    scope: str | None = None,
) -> User | None:
    tok = (token or "").strip()
    if not tok:
        return None

    res = await session.execute(select(MagicLinkToken).where(MagicLinkToken.token == tok))
    row = res.scalar_one_or_none()
    if not row:
        return None

    now = utc_now()
    exp = getattr(row, "expires_at", None)
    if exp is None or exp < now:
        return None

    if scope is not None:
        row_scope = getattr(row, "scope", None)
        if str(row_scope or "") != str(scope):
            return None

    res_u = await session.execute(select(User).where(User.id == int(row.user_id)).where(User.is_deleted == False))
    return res_u.scalar_one_or_none()


async def consume_magic_token(session: AsyncSession, *, token: str) -> User | None:
    # Backward-compatible alias: token validity is now determined only by expires_at.
    return await validate_magic_token(session, token=str(token))
