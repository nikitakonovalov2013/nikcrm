from __future__ import annotations

import html
import json
import logging
import calendar
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.db import add_after_commit_callback, get_async_session
from shared.enums import ShiftInstanceStatus
from shared.models import ShiftInstance, User
from shared.services.salaries_service import calc_user_period_totals
from shared.utils import utc_now


logger = logging.getLogger(__name__)


def shift_rating_callback_data(*, shift_id: int, rating: int) -> str:
    return f"shift_rate:{int(shift_id)}:{int(rating)}"


def shift_rating_keyboard_payload(*, shift_id: int) -> dict:
    row = []
    for n in range(1, 6):
        row.append({"text": f"⭐{int(n)}", "callback_data": shift_rating_callback_data(shift_id=int(shift_id), rating=int(n))})
    return {"inline_keyboard": [row]}


def shift_rating_stars(rating: int) -> str:
    n = max(1, min(5, int(rating)))
    return "⭐" * int(n)


def _is_shift_closed(shift: ShiftInstance) -> bool:
    st = getattr(shift, "status", None)
    if st in {ShiftInstanceStatus.CLOSED, ShiftInstanceStatus.APPROVED}:
        return True
    if getattr(shift, "ended_at", None) is not None:
        return True
    return False


def _shift_day_human(shift: ShiftInstance) -> str:
    d = getattr(shift, "day", None)
    if d is None:
        return ""
    try:
        return d.strftime("%d.%m.%Y")
    except Exception:
        return str(d)


def _month_period_for_day(d: date) -> tuple[date, date]:
    first = date(int(d.year), int(d.month), 1)
    last_day = int(calendar.monthrange(int(d.year), int(d.month))[1])
    last = date(int(d.year), int(d.month), int(last_day))
    return first, last


def _fmt_rub(v) -> str:
    try:
        n = float(v)
    except Exception:
        n = 0.0
    if abs(n - int(n)) < 1e-9:
        return f"{int(n)} ₽"
    return f"{n:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def _shift_day_ddmm(shift: ShiftInstance) -> str:
    d = getattr(shift, "day", None)
    if d is None:
        return "—"
    try:
        return d.strftime("%d.%m")
    except Exception:
        return str(d)


def shift_rating_request_text(*, shift: ShiftInstance, balance_rub: str) -> str:
    day_dm = _shift_day_ddmm(shift)
    return (
        f"Смена за {day_dm} завершена, спасибо за работу! ❤️\n\n"
        f"💰Ваш баланс: {balance_rub}\n\n"
        "Пожалуйста, оцените как прошла ваша смена: ⭐ 1–5"
    ).strip()


def shift_rating_result_text(*, shift: ShiftInstance, rating: int) -> str:
    day_s = _shift_day_human(shift)
    day_line = f"\nДата: <b>{html.escape(day_s)}</b>" if day_s else ""
    stars = shift_rating_stars(int(rating))
    return (
        "Смена завершена ✅\n"
        f"{day_line}\n\n"
        f"Оценка: {stars} ({int(rating)}/5)"
    ).strip()


async def set_shift_rating(
    *,
    session: AsyncSession,
    shift_id: int,
    user_id: int,
    rating: int,
) -> tuple[ShiftInstance | None, str]:
    n = int(rating)
    if n < 1 or n > 5:
        return None, "bad_rating"

    shift = (
        await session.execute(select(ShiftInstance).where(ShiftInstance.id == int(shift_id)))
    ).scalar_one_or_none()
    if shift is None:
        return None, "not_found"

    if int(getattr(shift, "user_id", 0) or 0) != int(user_id):
        return None, "forbidden"

    if not _is_shift_closed(shift):
        return None, "not_closed"

    shift.rating = int(n)
    shift.rated_at = utc_now()
    await session.flush()
    return shift, "ok"


async def _send_shift_rating_request_now(*, shift_id: int) -> None:
    async with get_async_session() as session:
        shift = (
            await session.execute(
                select(ShiftInstance).where(ShiftInstance.id == int(shift_id))
            )
        ).scalar_one_or_none()
        if shift is None:
            return

        if not _is_shift_closed(shift):
            return

        if getattr(shift, "rating", None) is not None:
            return

        if getattr(shift, "rating_requested_at", None) is not None:
            return

        user = (
            await session.execute(select(User).where(User.id == int(getattr(shift, "user_id", 0) or 0)))
        ).scalar_one_or_none()
        if user is None:
            return
        chat_id = int(getattr(user, "tg_id", 0) or 0)
        if chat_id <= 0:
            return

        token = str(getattr(settings, "BOT_TOKEN", "") or "").strip()
        if not token:
            return

        balance_rub = _fmt_rub(0)
        try:
            ps, pe = _month_period_for_day(date.today())
            totals = await calc_user_period_totals(
                session=session,
                user_id=int(getattr(user, "id", 0) or 0),
                period_start=ps,
                period_end=pe,
            )
            balance_rub = _fmt_rub(getattr(totals, "balance", 0) or 0)
        except Exception:
            pass

        text = shift_rating_request_text(shift=shift, balance_rub=balance_rub)
        kb = shift_rating_keyboard_payload(shift_id=int(getattr(shift, "id", 0) or 0))

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": int(chat_id),
            "text": str(text),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": json.dumps(kb, ensure_ascii=False),
        }

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, data=payload)
            r.raise_for_status()
            data = r.json() if r.content else {}

        if not bool((data or {}).get("ok")):
            return

        msg = (data or {}).get("result") or {}
        shift.rating_message_id = int(msg.get("message_id", 0) or 0) or None
        shift.rating_requested_at = utc_now()
        await session.flush()


def schedule_shift_rating_request_after_commit(*, session: AsyncSession, shift_id: int) -> None:
    sid = int(shift_id)

    async def _cb() -> None:
        try:
            await _send_shift_rating_request_now(shift_id=int(sid))
        except Exception:
            logger.exception("shift rating request failed", extra={"shift_id": int(sid)})

    add_after_commit_callback(session, _cb)
