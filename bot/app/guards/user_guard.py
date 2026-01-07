from __future__ import annotations

import logging
from urllib.parse import urlparse

from aiogram.types import CallbackQuery, Message

from shared.config import settings
from shared.db import get_async_session
from shared.enums import UserStatus

from bot.app.keyboards.main import main_menu_kb
from bot.app.repository.users import UserRepository

_logger = logging.getLogger(__name__)


def extract_tg_id(event: Message | CallbackQuery) -> int:
    return int(event.from_user.id)


def _safe_dsn_parts(dsn: str) -> dict[str, str | None]:
    try:
        u = urlparse(dsn)
        return {
            "host": u.hostname,
            "port": str(u.port) if u.port else None,
            "db": (u.path or "").lstrip("/") or None,
        }
    except Exception:
        return {"host": None, "port": None, "db": None}


async def ensure_registered_or_reply(event: Message | CallbackQuery):
    """Single source of truth for 'registered/ok to use bot features'.

    Rules:
    - lookup is always by from_user.id
    - first lookup is without any filters (no is_deleted/approved)
    - only if not found -> 'Вы не зарегистрированы'
    - DB exceptions are logged and shown as server error (not 'not registered')
    """

    tg_id = extract_tg_id(event)

    try:
        async with get_async_session() as session:
            repo = UserRepository(session)
            user = await repo.get_by_tg_id_any(tg_id)
    except Exception:
        _logger.exception("ensure_registered db error", extra={"tg_id": tg_id, "dsn": _safe_dsn_parts(settings.DATABASE_URL or "")})
        if isinstance(event, Message):
            await event.answer("⚠️ Ошибка сервера, попробуйте позже.")
        else:
            try:
                await event.answer("⚠️ Ошибка сервера, попробуйте позже.", show_alert=True)
            except Exception:
                pass
        return None

    _logger.debug(
        "ensure_registered",
        extra={
            "tg_id": tg_id,
            "dsn": _safe_dsn_parts(settings.DATABASE_URL or ""),
            "found": bool(user is not None),
            "user_id": int(getattr(user, "id", 0)) if user else None,
            "is_deleted": bool(getattr(user, "is_deleted", False)) if user else None,
            "status": str(getattr(user, "status", None)) if user else None,
            "position": str(getattr(user, "position", None)) if user else None,
        },
    )

    if user is None:
        if isinstance(event, Message):
            await event.answer("ℹ️ Вы не зарегистрированы.", reply_markup=main_menu_kb(None, tg_id))
        else:
            try:
                await event.answer("ℹ️ Вы не зарегистрированы.", show_alert=True)
            except Exception:
                pass
        return None

    if bool(getattr(user, "is_deleted", False)):
        if isinstance(event, Message):
            await event.answer(
                "🚫 Аккаунт удалён/заблокирован.",
                reply_markup=main_menu_kb(getattr(user, "status", None), tg_id, getattr(user, "position", None)),
            )
        else:
            try:
                await event.answer("🚫 Аккаунт удалён/заблокирован.", show_alert=True)
            except Exception:
                pass
        return None

    if user.status == UserStatus.BLACKLISTED:
        if isinstance(event, Message):
            await event.answer(
                "🚫 Доступ ограничен.",
                reply_markup=main_menu_kb(user.status, tg_id, user.position),
            )
        else:
            try:
                await event.answer("🚫 Доступ ограничен.", show_alert=True)
            except Exception:
                pass
        return None

    if user.status != UserStatus.APPROVED:
        if isinstance(event, Message):
            await event.answer(
                "⏳ Ожидает одобрения.",
                reply_markup=main_menu_kb(user.status, tg_id, user.position),
            )
        else:
            try:
                await event.answer("⏳ Ожидает одобрения.", show_alert=True)
            except Exception:
                pass
        return None

    return user
