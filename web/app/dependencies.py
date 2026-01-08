from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.enums import Position, UserStatus
from shared.models import User


def _decode_admin_token(request: Request) -> dict:
    token = request.cookies.get("admin_token")
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    try:
        data = jwt.decode(token, settings.WEB_JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    exp = data.get("exp")
    if exp and datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    return data


def require_admin(request: Request) -> int:
    data = _decode_admin_token(request)
    if data.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    sub = int(data.get("sub"))
    if sub not in settings.admin_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    return sub


def require_user(request: Request) -> int:
    """Allow any authenticated CRM user (admin/manager/staff).

    Used for public task board (/crm/tasks/public) and task APIs that should be available
    to regular employees.
    """

    data = _decode_admin_token(request)
    role = data.get("role")
    sub = int(data.get("sub"))

    if role == "admin":
        if sub not in settings.admin_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return sub

    if role == "manager":
        # validated by ensure_manager_allowed in handlers with DB session
        return sub

    if role == "staff":
        return sub

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def require_staff(request: Request) -> int:
    data = _decode_admin_token(request)

    role = data.get("role")
    sub = int(data.get("sub"))

    if role == "admin":
        if sub not in settings.admin_ids:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
        return sub

    if role == "manager":
        # validated by ensure_manager_allowed in handlers with DB session
        return sub

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


async def ensure_manager_allowed(request: Request, staff_tg_id: int, session: AsyncSession) -> None:
    """If JWT role is manager, validate that tg_id belongs to APPROVED manager in DB."""
    data = _decode_admin_token(request)
    if data.get("role") != "manager":
        return

    u = (
        (await session.execute(select(User).where(User.tg_id == int(staff_tg_id)).where(User.is_deleted == False)))
    ).scalar_one_or_none()
    if not u or u.status != UserStatus.APPROVED or u.position != Position.MANAGER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)


def require_authenticated_user(request: Request) -> int:
    """Allow any authenticated CRM user (admin/manager/staff)."""

    return require_user(request)


def require_admin_or_manager(request: Request) -> int:
    """Allow only admin/manager (full CRM access)."""

    return require_staff(request)
