from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

from sqlalchemy import select

from shared.db import get_async_session
from shared.models import Broadcast, BroadcastRating
from shared.utils import utc_now

from bot.app.guards.user_guard import ensure_registered_or_reply


router = Router()
_logger = logging.getLogger(__name__)


def _rating_pick_kb(*, broadcast_id: int) -> dict:
    row = []
    for n in range(1, 6):
        row.append({"text": f"⭐{n}", "callback_data": f"broadcast_rate_set:{int(broadcast_id)}:{int(n)}"})
    return {"inline_keyboard": [row]}


def _stars(n: int) -> str:
    n0 = max(0, min(5, int(n)))
    return "⭐" * n0


@router.callback_query(F.data.startswith("broadcast_rate:"))
async def cb_broadcast_rate(cb: CallbackQuery):
    try:
        await cb.answer()
    except Exception:
        pass

    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    parts = str(cb.data or "").split(":")
    if len(parts) != 2:
        return
    try:
        bid = int(parts[1])
    except Exception:
        return

    async with get_async_session() as session:
        b = (await session.execute(select(Broadcast).where(Broadcast.id == int(bid)))).scalar_one_or_none()
        if b is None:
            try:
                await cb.answer("Новость не найдена", show_alert=True)
            except Exception:
                pass
            return

    if cb.message:
        try:
            await cb.message.edit_reply_markup(reply_markup=_rating_pick_kb(broadcast_id=int(bid)))
        except Exception:
            pass


@router.callback_query(F.data.startswith("broadcast_rate_set:"))
async def cb_broadcast_rate_set(cb: CallbackQuery):
    parts = str(cb.data or "").split(":")
    if len(parts) != 3:
        try:
            await cb.answer("Ошибка")
        except Exception:
            pass
        return

    try:
        bid = int(parts[1])
        rating = int(parts[2])
    except Exception:
        try:
            await cb.answer("Ошибка")
        except Exception:
            pass
        return

    if rating < 1 or rating > 5:
        try:
            await cb.answer("Оценка должна быть 1–5", show_alert=True)
        except Exception:
            pass
        return

    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    async with get_async_session() as session:
        b = (await session.execute(select(Broadcast).where(Broadcast.id == int(bid)))).scalar_one_or_none()
        if b is None:
            try:
                await cb.answer("Новость не найдена", show_alert=True)
            except Exception:
                pass
            return

        r = (
            await session.execute(
                select(BroadcastRating)
                .where(BroadcastRating.broadcast_id == int(bid))
                .where(BroadcastRating.user_id == int(user.id))
            )
        ).scalar_one_or_none()
        if r is None:
            r = BroadcastRating(broadcast_id=int(bid), user_id=int(user.id), rating=int(rating), rated_at=utc_now())
            session.add(r)
        else:
            r.rating = int(rating)
            r.rated_at = utc_now()
        await session.flush()

    try:
        await cb.answer(f"Спасибо! Ваша оценка: {_stars(rating)}")
    except Exception:
        pass

    if cb.message:
        try:
            await cb.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
