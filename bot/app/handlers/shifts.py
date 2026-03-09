from __future__ import annotations

import logging
import asyncio
from datetime import datetime, date, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from sqlalchemy import select

from shared.config import settings
from shared.db import get_async_session, add_after_commit_callback
from shared.enums import UserStatus, ShiftInstanceStatus, Position
from shared.permissions import role_flags
from shared.utils import MOSCOW_TZ, utc_now
from shared.models import User, WorkShiftDay, ShiftInstance, ShiftInstanceEvent

from shared.services.shifts_domain import is_shift_active_status, is_shift_final_status
from shared.services.shifts_service import get_today_working_staff_with_open_state
from shared.services.shifts_rating import (
    schedule_shift_rating_request_after_commit,
    set_shift_rating,
    shift_rating_result_text,
    shift_rating_stars,
)

from bot.app.guards.user_guard import ensure_registered_or_reply
from bot.app.keyboards.main import main_menu_kb
from bot.app.utils.telegram import edit_html, send_html, send_new_and_delete_active
from bot.app.utils.urls import build_schedule_magic_link
from bot.app.utils.html import format_plain_url, esc
from bot.app.states.shifts import ShiftCloseEditState, ShiftManagerEditState


router = Router()
_logger = logging.getLogger(__name__)


def _user_name(u: User | None) -> str:
    if not u:
        return "—"
    first = str(getattr(u, "first_name", "") or "").strip()
    last = str(getattr(u, "last_name", "") or "").strip()
    full = (first + " " + last).strip()
    return full or f"User #{int(getattr(u, 'id', 0) or 0)}"


def _fmt_day(d: date | None) -> str:
    if not d:
        return "—"
    try:
        return d.strftime("%d.%m.%Y")
    except Exception:
        return str(d)


def _fmt_plan_interval(*, day: date | None, start_time, end_time, is_emergency: bool) -> str:
    ds = _fmt_day(day)
    if start_time and end_time:
        try:
            return f"{ds} • {start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}"
        except Exception:
            return f"{ds}"
    if is_emergency:
        return f"{ds} • ⚡ экстренная смена"
    return f"{ds} • вне расписания"


def _fmt_hhmm(t: object | None) -> str:
    try:
        if t is None:
            return ""
        if hasattr(t, "strftime"):
            return t.strftime("%H:%M")
    except Exception:
        return ""
    return ""


def _fmt_work_line(*, name: str, start_time: object | None, end_time: object | None, planned_hours: int | None, is_opened: bool) -> str:
    dot = "🟢" if is_opened else "🔴"
    st = _fmt_hhmm(start_time)
    en = _fmt_hhmm(end_time)
    if st and en:
        interval = f"{st}–{en}"
    elif planned_hours is not None:
        interval = f"{int(planned_hours)} ч."
    else:
        interval = "—"
    return f"{dot} {esc(name)} — {esc(interval)}"


async def _notify_admins_and_managers_about_shift_event(
    *,
    bot,
    actor_user_id: int,
    text: str,
) -> None:
    try:
        async with get_async_session() as session:
            q = (
                select(User)
                .where(User.is_deleted == False)  # noqa: E712
                .where(
                    (User.tg_id.in_([int(x) for x in (settings.admin_ids or [])]))
                    | ((User.status == UserStatus.APPROVED) & (User.position == Position.MANAGER))
                )
                .where(User.id != int(actor_user_id))
            )
            users = list((await session.execute(q)).scalars().all())

        chat_ids: list[int] = []
        for u in users:
            tg_id = getattr(u, "tg_id", None)
            if not tg_id:
                continue
            chat_ids.append(int(tg_id))

        async def _send_one(cid: int):
            try:
                await bot.send_message(chat_id=int(cid), text=str(text))
            except Exception:
                _logger.warning("failed to send shift notify", extra={"chat_id": int(cid)}, exc_info=True)

        await asyncio.gather(*[_send_one(cid) for cid in chat_ids], return_exceptions=True)
    except Exception:
        _logger.exception("shift notify failed")


async def _cb_answer_safely(cb: CallbackQuery, text: str | None = None) -> None:
    try:
        await cb.answer(text or "")
    except Exception:
        pass


@router.callback_query(F.data == "noop")
async def _noop(cb: CallbackQuery) -> None:
    try:
        await cb.answer()
    except Exception:
        pass


@router.callback_query(F.data.startswith("shift_rate:"))
async def shift_rate_set(cb: CallbackQuery) -> None:
    parts = str(cb.data or "").split(":")
    if len(parts) != 3:
        await _cb_answer_safely(cb, "Ошибка")
        return

    try:
        shift_id = int(parts[1])
        rating = int(parts[2])
    except Exception:
        await _cb_answer_safely(cb, "Ошибка")
        return

    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    async with get_async_session() as session:
        shift, code = await set_shift_rating(
            session=session,
            shift_id=int(shift_id),
            user_id=int(getattr(user, "id", 0) or 0),
            rating=int(rating),
        )

    if code == "bad_rating":
        await _cb_answer_safely(cb, "Оценка должна быть 1–5")
        return
    if code == "not_found":
        await _cb_answer_safely(cb, "Смена не найдена")
        return
    if code == "forbidden":
        await _cb_answer_safely(cb, "Нет доступа")
        return
    if code == "not_closed":
        await _cb_answer_safely(cb, "Смена ещё не завершена")
        return

    await _cb_answer_safely(cb, f"Спасибо! Оценка: {shift_rating_stars(int(rating))}")

    if cb.message and shift is not None:
        text = shift_rating_result_text(shift=shift, rating=int(rating))
        try:
            await cb.message.edit_text(text=str(text), parse_mode="HTML", reply_markup=None)
        except Exception:
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass


def _kb_schedule_return() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📅 Меню графика", callback_data="sched_menu:open")],
        ]
    )


def _kb_pending_nav(*, page: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    nav: list[InlineKeyboardButton] = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="←", callback_data=f"sched_pending:page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"Стр. {page+1}", callback_data="noop"))
    if has_next:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"sched_pending:page:{page+1}"))

    rows: list[list[InlineKeyboardButton]] = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="📅 Меню графика", callback_data="sched_menu:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_pending_item(*, shift_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"sched_pending:approve:{shift_id}:{page}"),
                InlineKeyboardButton(text="✏️ Изменить сумму", callback_data=f"sched_pending:edit:{shift_id}:{page}"),
            ],
            [InlineKeyboardButton(text="🔁 На доработку", callback_data=f"sched_pending:rework:{shift_id}:{page}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"sched_pending:page:{page}")],
        ]
    )


async def _render_pending_page(*, session, page: int) -> tuple[str, InlineKeyboardMarkup]:
    size = 1
    offset = max(0, int(page)) * size
    res = await session.execute(
        select(ShiftInstance, User)
        .join(User, User.id == ShiftInstance.user_id)
        .where(ShiftInstance.status == ShiftInstanceStatus.PENDING_APPROVAL)
        .order_by(ShiftInstance.day.desc(), ShiftInstance.id.desc())
        .offset(offset)
        .limit(size + 1)
    )
    rows = list(res.all())
    has_next = len(rows) > size
    rows = rows[:size]
    has_prev = offset > 0

    if not rows:
        text = "✅ <b>На подтверждении</b>\n\nНет смен на подтверждении."
        return text, _kb_pending_nav(page=page, has_prev=has_prev, has_next=has_next)

    shift, staff = rows[0]
    staff_name = (
        " ".join([str(getattr(staff, "first_name", "") or "").strip(), str(getattr(staff, "last_name", "") or "").strip()]).strip()
        or f"User #{int(getattr(staff, 'id'))}"
    )
    text = (
        "✅ <b>На подтверждении</b>\n\n"
        f"🧾 <b>{esc(staff_name)}</b>\n"
        f"Дата: <b>{shift.day}</b>\n"
        f"Расчёт: <b>{int(getattr(shift,'amount_default',0) or 0)} ₽</b>\n"
        f"Заявка: <b>{int(getattr(shift,'amount_submitted',0) or 0)} ₽</b>\n"
    )
    return text, _kb_pending_item(shift_id=int(shift.id), page=page)


def _kb_open_shift(*, day: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Начать смену", callback_data=f"shift:start:{day}")],
        ]
    )


def _kb_close_shift(*, shift_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏹ Закрыть смену", callback_data=f"shift:close:{shift_id}"),
            ],
        ]
    )


def _kb_close_confirm(*, shift_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data=f"shift:close_ok:{shift_id}"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data=f"shift:close_edit:{shift_id}"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="shift:cancel")],
        ]
    )


def _kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="shift:cancel")]])


def _kb_cancel_skip(*, skip_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data=str(skip_data))],
            [InlineKeyboardButton(text="Отмена", callback_data="shift:cancel")],
        ]
    )


async def _log_event(session, *, shift_id: int, actor_user_id: int | None, type: str, payload: dict | None = None) -> None:
    session.add(
        ShiftInstanceEvent(
            shift_id=int(shift_id),
            actor_user_id=int(actor_user_id) if actor_user_id is not None else None,
            type=str(type),
            payload=(payload or None),
        )
    )
    await session.flush()


def _calc_default_amount(
    *,
    base_rate: int,
    extra_hours: int,
    extra_hour_rate: int,
    overtime_hours: int,
    overtime_hour_rate: int,
) -> int:
    return int(base_rate) + int(extra_hours) * int(extra_hour_rate) + int(overtime_hours) * int(overtime_hour_rate)


async def _get_today_plan(session, *, user_id: int, day: date) -> WorkShiftDay | None:
    return (
        await session.execute(
            select(WorkShiftDay)
            .where(WorkShiftDay.user_id == int(user_id))
            .where(WorkShiftDay.day == day)
        )
    ).scalar_one_or_none()


async def _get_active_shift(session, *, user_id: int) -> ShiftInstance | None:
    # Prefer started shift (latest)
    return (
        await session.execute(
            select(ShiftInstance)
            .where(ShiftInstance.user_id == int(user_id))
            .where(ShiftInstance.status == ShiftInstanceStatus.STARTED)
            .order_by(ShiftInstance.day.desc(), ShiftInstance.id.desc())
        )
    ).scalar_one_or_none()


@router.callback_query(F.data == "shift:cancel")
async def shift_cancel(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    await state.clear()
    await edit_html(cb, "Отменено.", reply_markup=_kb_schedule_return())


@router.callback_query(F.data.startswith("shift:skip:"))
async def shift_skip(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    await state.clear()
    await edit_html(cb, "Хорошо. Если планы изменятся — откройте меню графика в любое время.", reply_markup=_kb_schedule_return())


@router.callback_query(F.data.startswith("shift:start:"))
async def shift_start(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    user = await ensure_registered_or_reply(cb)
    if not user:
        return
    r = role_flags(tg_id=int(cb.from_user.id), admin_ids=settings.admin_ids, status=user.status, position=user.position)
    if user.status != UserStatus.APPROVED and not (bool(r.is_admin) or bool(r.is_manager)):
        await edit_html(cb, "⛔ Нет доступа.")
        return

    day_s = str(cb.data or "").split(":", 2)[2]
    try:
        d = datetime.strptime(day_s, "%Y-%m-%d").date()
    except Exception:
        d = datetime.now(MOSCOW_TZ).date()

    async with get_async_session() as session:
        # Prevent double-start
        existing = (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.user_id == int(user.id))
                .where(ShiftInstance.day == d)
            )
        ).scalar_one_or_none()

        if existing is not None:
            if is_shift_final_status(getattr(existing, "status", None), ended_at=getattr(existing, "ended_at", None)):
                await state.clear()
                await edit_html(
                    cb,
                    "Смена уже завершена ✅\n\nОбнови меню «График работы».",
                    reply_markup=_kb_schedule_return(),
                )
                return

        if existing is not None and is_shift_active_status(getattr(existing, "status", None), ended_at=getattr(existing, "ended_at", None)):
            await edit_html(cb, "Смена уже открыта.", reply_markup=_kb_close_shift(shift_id=int(existing.id)))
            return

        plan = await _get_today_plan(session, user_id=int(user.id), day=d)
        planned_hours = int(getattr(plan, "hours", 0) or 0) or None
        is_emergency = False
        if plan is None or str(getattr(plan, "kind", "")) != "work":
            is_emergency = True

        start_time = getattr(plan, "start_time", None) if plan is not None else None
        end_time = getattr(plan, "end_time", None) if plan is not None else None

        now = utc_now()

        if existing is None:
            row = ShiftInstance(
                user_id=int(user.id),
                day=d,
                planned_hours=planned_hours,
                is_emergency=is_emergency,
                started_at=now,
                status=ShiftInstanceStatus.STARTED,
                base_rate=int(getattr(user, "rate_k", 0) or 0),
            )
            session.add(row)
            await session.flush()
            await _log_event(session, shift_id=int(row.id), actor_user_id=int(user.id), type="Смена открыта")
            shift = row
        else:
            existing.started_at = now
            existing.status = ShiftInstanceStatus.STARTED
            if existing.base_rate is None:
                existing.base_rate = int(getattr(user, "rate_k", 0) or 0)
            await session.flush()
            await _log_event(session, shift_id=int(existing.id), actor_user_id=int(user.id), type="Смена открыта")
            shift = existing

        interval = _fmt_plan_interval(day=d, start_time=start_time, end_time=end_time, is_emergency=bool(is_emergency))
        staff_name = _user_name(user)
        notify_text = f"✅ Смена открыта: {esc(staff_name)} • {esc(interval)}"

        async def _after_commit() -> None:
            await _notify_admins_and_managers_about_shift_event(
                bot=cb.bot,
                actor_user_id=int(user.id),
                text=str(notify_text),
            )

        add_after_commit_callback(session, _after_commit)

        today_staff = await get_today_working_staff_with_open_state(session=session, day=d)

    await state.clear()

    lines: list[str] = [
        "✅ Смена открыта",
        "",
    ]
    if not today_staff:
        lines.append("Сегодня кроме вас смен нет")
    else:
        lines.append("Сегодня работают:")
        for row in today_staff:
            try:
                lines.append(
                    _fmt_work_line(
                        name=str(getattr(row, "full_name", "") or "—"),
                        start_time=getattr(row, "start_time", None),
                        end_time=getattr(row, "end_time", None),
                        planned_hours=getattr(row, "planned_hours", None),
                        is_opened=bool(getattr(row, "is_opened", False)),
                    )
                )
            except Exception:
                continue

    await edit_html(
        cb,
        "\n".join(lines),
        reply_markup=_kb_schedule_return(),
    )


async def _shift_close_prompt_by_id(cb: CallbackQuery, state: FSMContext, *, shift_id: int) -> None:
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    async with get_async_session() as session:
        shift = (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.id == int(shift_id))
            )
        ).scalar_one_or_none()

        if shift is None:
            await state.clear()
            await edit_html(
                cb,
                "Смена не найдена или уже изменена. Обнови меню «График работы».",
                reply_markup=_kb_schedule_return(),
            )
            return

        # Permissions: owner or admin/manager
        r = role_flags(
            tg_id=int(cb.from_user.id),
            admin_ids=settings.admin_ids,
            status=user.status,
            position=user.position,
        )
        if int(getattr(shift, "user_id")) != int(user.id) and not (r.is_admin or r.is_manager):
            await edit_html(cb, "⛔ Нет доступа.")
            return

        if shift.status != ShiftInstanceStatus.STARTED:
            await state.clear()
            await edit_html(
                cb,
                "Смена не в статусе 'Открыта'. Обнови меню «График работы».",
                reply_markup=_kb_schedule_return(),
            )
            return

        base_rate = int(getattr(shift, "base_rate", None) or int(getattr(user, "rate_k", 0) or 0))
        extra_hours = int(getattr(shift, "extra_hours", 0) or 0)
        overtime_hours = int(getattr(shift, "overtime_hours", 0) or 0)
        extra_rate = int(getattr(shift, "extra_hour_rate", 300) or 300)
        overtime_rate = int(getattr(shift, "overtime_hour_rate", 400) or 400)
        amount_default = _calc_default_amount(
            base_rate=base_rate,
            extra_hours=extra_hours,
            extra_hour_rate=extra_rate,
            overtime_hours=overtime_hours,
            overtime_hour_rate=overtime_rate,
        )

        shift.base_rate = base_rate
        shift.amount_default = amount_default
        await session.flush()

    await state.clear()
    await edit_html(
        cb,
        f"Закрываем смену. Сумма по умолчанию: <b>{amount_default} ₽</b>. Подтвердить?",
        reply_markup=_kb_close_confirm(shift_id=int(shift_id)),
    )


@router.callback_query(F.data.startswith("shift:close:"))
async def shift_close_prompt(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    try:
        shift_id = int(str(cb.data).split(":", 2)[2])
    except Exception:
        await state.clear()
        await edit_html(
            cb,
            "Смена не найдена или уже изменена. Обнови меню «График работы».",
            reply_markup=_kb_schedule_return(),
        )
        return

    await _shift_close_prompt_by_id(cb, state, shift_id=int(shift_id))


@router.callback_query(F.data.startswith("sch:finish:"))
async def schedule_finish_by_shift_id(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    try:
        shift_id = int(str(cb.data).split(":", 2)[2])
    except Exception:
        await state.clear()
        await edit_html(
            cb,
            "Смена не найдена или уже изменена. Обнови меню «График работы».",
            reply_markup=_kb_schedule_return(),
        )
        return

    await _shift_close_prompt_by_id(cb, state, shift_id=int(shift_id))


@router.callback_query(F.data.startswith("shift:close_by_day:"))
async def shift_close_by_day(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    user = await ensure_registered_or_reply(cb)
    if not user:
        return
    r = role_flags(tg_id=int(cb.from_user.id), admin_ids=settings.admin_ids, status=user.status, position=user.position)
    if user.status != UserStatus.APPROVED and not (bool(r.is_admin) or bool(r.is_manager)):
        await edit_html(cb, "⛔ Нет доступа.")
        return

    day_s = str(cb.data or "").split(":", 2)[2]
    try:
        d = datetime.strptime(day_s, "%Y-%m-%d").date()
    except Exception:
        d = datetime.now(MOSCOW_TZ).date()

    async with get_async_session() as session:
        shift = (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.user_id == int(user.id))
                .where(ShiftInstance.day == d)
                .order_by(ShiftInstance.id.desc())
            )
        ).scalar_one_or_none()

        if shift is None:
            await state.clear()
            await edit_html(cb, "Смена за этот день не найдена. Если вы ещё не начинали — откройте смену в меню графика.", reply_markup=_kb_schedule_return())
            return
        if shift.status != ShiftInstanceStatus.STARTED:
            await state.clear()
            await edit_html(cb, "Смена не в статусе 'Открыта'. Откройте меню графика для проверки.", reply_markup=_kb_schedule_return())
            return

        shift_id = int(getattr(shift, "id"))

    await _shift_close_prompt_by_id(cb, state, shift_id=int(shift_id))


@router.callback_query(F.data.startswith("shift:end_snooze:"))
async def shift_end_snooze(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    user = await ensure_registered_or_reply(cb)
    if not user:
        return
    r = role_flags(tg_id=int(cb.from_user.id), admin_ids=settings.admin_ids, status=user.status, position=user.position)
    if user.status != UserStatus.APPROVED and not (bool(r.is_admin) or bool(r.is_manager)):
        await edit_html(cb, "⛔ Нет доступа.")
        return

    day_s = str(cb.data or "").split(":", 2)[2]
    try:
        d = datetime.strptime(day_s, "%Y-%m-%d").date()
    except Exception:
        d = datetime.now(MOSCOW_TZ).date()

    now = utc_now()
    async with get_async_session() as session:
        wsd = (
            await session.execute(
                select(WorkShiftDay)
                .where(WorkShiftDay.user_id == int(user.id))
                .where(WorkShiftDay.day == d)
                .where(WorkShiftDay.kind == "work")
            )
        ).scalar_one_or_none()
        if wsd is None:
            await state.clear()
            await edit_html(cb, "План смены не найден.", reply_markup=_kb_schedule_return())
            return

        wsd.end_snooze_until = now + timedelta(hours=1)
        wsd.end_followup_notified_at = None
        await session.flush()

    await state.clear()
    await edit_html(cb, "Ок. Напомню через 1 час.", reply_markup=_kb_schedule_return())


@router.callback_query(F.data.startswith("shift:close_ok:"))
async def shift_close_ok(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    shift_id = int(str(cb.data).split(":", 2)[2])

    async with get_async_session() as session:
        shift = (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.id == int(shift_id))
                .where(ShiftInstance.user_id == int(user.id))
            )
        ).scalar_one_or_none()
        if shift is None:
            await edit_html(cb, "Смена не найдена.")
            return

        plan = await _get_today_plan(session, user_id=int(user.id), day=getattr(shift, "day", None))
        start_time = getattr(plan, "start_time", None) if plan is not None else None
        end_time = getattr(plan, "end_time", None) if plan is not None else None
        is_emergency = bool(getattr(shift, "is_emergency", False) or (plan is None) or (str(getattr(plan, "kind", "")) != "work"))

        now = utc_now()
        shift.ended_at = now
        shift.status = ShiftInstanceStatus.APPROVED
        shift.amount_approved = int(getattr(shift, "amount_default", 0) or 0)
        shift.amount_submitted = shift.amount_approved
        shift.approval_required = False
        await session.flush()
        schedule_shift_rating_request_after_commit(session=session, shift_id=int(shift.id))
        await _log_event(session, shift_id=int(shift.id), actor_user_id=int(user.id), type="Смена закрыта")
        await _log_event(session, shift_id=int(shift.id), actor_user_id=int(user.id), type="Сумма подтверждена руководителем")

        interval = _fmt_plan_interval(day=getattr(shift, "day", None), start_time=start_time, end_time=end_time, is_emergency=is_emergency)
        staff_name = _user_name(user)
        planned_hours = int(getattr(shift, "planned_hours", 0) or 0)
        extra_hours = int(getattr(shift, "extra_hours", 0) or 0)
        overtime_hours = int(getattr(shift, "overtime_hours", 0) or 0)
        fact_hours = planned_hours + extra_hours + overtime_hours
        parts: list[str] = []
        parts.append(f"🛑 Смена закрыта: {esc(staff_name)} • {esc(interval)}")
        if fact_hours > 0:
            tail = f"факт: {int(fact_hours)} ч"
            if extra_hours:
                tail += f" (+{int(extra_hours)} ч доп)"
            if overtime_hours:
                tail += f" (+{int(overtime_hours)} ч вне графика)"
            parts.append(tail)
        notify_text = " • ".join(parts) if len(parts) == 1 else (parts[0] + " • " + parts[1])

        async def _after_commit() -> None:
            await _notify_admins_and_managers_about_shift_event(
                bot=cb.bot,
                actor_user_id=int(user.id),
                text=str(notify_text),
            )

        add_after_commit_callback(session, _after_commit)

    await state.clear()
    await edit_html(
        cb,
        f"✅ Смена закрыта. Итог: <b>{int(shift.amount_approved or 0)} ₽</b>.",
        reply_markup=_kb_schedule_return(),
    )


@router.callback_query(F.data.startswith("shift:close_edit:"))
async def shift_close_edit(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    shift_id = int(str(cb.data).split(":", 2)[2])
    await state.clear()
    await state.set_state(ShiftCloseEditState.extra_hours)
    await state.update_data(
        shift_id=shift_id,
        active_bot_chat_id=int(cb.message.chat.id) if cb.message else None,
        active_bot_message_id=int(cb.message.message_id) if cb.message else None,
    )
    await edit_html(
        cb,
        "Сколько доп. часов в рамках смены? (0..N)",
        reply_markup=_kb_cancel_skip(skip_data="shift:extra_hours_skip"),
    )


@router.callback_query(F.data == "shift:extra_hours_skip")
async def shift_close_edit_extra_hours_skip(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    await state.update_data(extra_hours=0)
    await state.set_state(ShiftCloseEditState.overtime_hours)
    await edit_html(
        cb,
        "Сколько часов вне графика? (0..N)",
        reply_markup=_kb_cancel_skip(skip_data="shift:overtime_hours_skip"),
    )


@router.message(ShiftCloseEditState.extra_hours)
async def shift_close_edit_extra_hours(message: Message, state: FSMContext):
    try:
        raw = str(message.text or "").strip()
        if raw in {"-", "—"}:
            eh = 0
        else:
            eh = int(raw)
        if eh < 0:
            raise ValueError
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(
            message=message,
            state=state,
            text="Введите число 0 или больше.",
            reply_markup=_kb_cancel(),
        )
        return

    await state.update_data(extra_hours=eh)
    await state.set_state(ShiftCloseEditState.overtime_hours)
    try:
        await message.delete()
    except Exception:
        pass
    await send_new_and_delete_active(
        message=message,
        state=state,
        text="Сколько часов вне графика? (0..N)",
        reply_markup=_kb_cancel_skip(skip_data="shift:overtime_hours_skip"),
    )


@router.callback_query(F.data == "shift:overtime_hours_skip")
async def shift_close_edit_overtime_skip(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    await state.update_data(overtime_hours=0)
    await state.set_state(ShiftCloseEditState.amount)
    await edit_html(cb, "Введите итоговую сумму (₽):", reply_markup=_kb_cancel())


@router.message(ShiftCloseEditState.overtime_hours)
async def shift_close_edit_overtime(message: Message, state: FSMContext):
    try:
        raw = str(message.text or "").strip()
        if raw in {"-", "—"}:
            ov = 0
        else:
            ov = int(raw)
        if ov < 0:
            raise ValueError
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(
            message=message,
            state=state,
            text="Введите число 0 или больше.",
            reply_markup=_kb_cancel(),
        )
        return

    await state.update_data(overtime_hours=ov)
    await state.set_state(ShiftCloseEditState.amount)
    try:
        await message.delete()
    except Exception:
        pass
    await send_new_and_delete_active(
        message=message,
        state=state,
        text="Введите итоговую сумму (₽):",
        reply_markup=_kb_cancel(),
    )


@router.message(ShiftCloseEditState.amount)
async def shift_close_edit_amount(message: Message, state: FSMContext):
    try:
        amt = int(str(message.text or "").strip())
        if amt < 0:
            raise ValueError
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(
            message=message,
            state=state,
            text="Введите сумму числом (>=0).",
            reply_markup=_kb_cancel(),
        )
        return

    await state.update_data(amount=amt)
    await state.set_state(ShiftCloseEditState.comment)
    try:
        await message.delete()
    except Exception:
        pass
    await send_new_and_delete_active(
        message=message,
        state=state,
        text="Комментарий (необязательно). Можно пропустить кнопкой ниже.",
        reply_markup=_kb_cancel_skip(skip_data="shift:comment_skip"),
    )


@router.callback_query(F.data == "shift:comment_skip")
async def shift_close_edit_comment_skip(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    data = await state.get_data()
    shift_id = int(data.get("shift_id") or 0)
    extra_hours = int(data.get("extra_hours") or 0)
    overtime_hours = int(data.get("overtime_hours") or 0)
    amount = int(data.get("amount") or 0)
    comment = None

    async with get_async_session() as session:
        shift = (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.id == int(shift_id))
                .where(ShiftInstance.user_id == int(user.id))
            )
        ).scalar_one_or_none()
        if shift is None:
            await state.clear()
            await edit_html(cb, "Смена не найдена.", reply_markup=_kb_schedule_return())
            return

        plan = await _get_today_plan(session, user_id=int(user.id), day=getattr(shift, "day", None))
        start_time = getattr(plan, "start_time", None) if plan is not None else None
        end_time = getattr(plan, "end_time", None) if plan is not None else None
        is_emergency = bool(getattr(shift, "is_emergency", False) or (plan is None) or (str(getattr(plan, "kind", "")) != "work"))

        base_rate = int(getattr(shift, "base_rate", None) or int(getattr(user, "rate_k", 0) or 0))
        extra_rate = int(getattr(shift, "extra_hour_rate", 300) or 300)
        overtime_rate = int(getattr(shift, "overtime_hour_rate", 400) or 400)
        amount_default = int(
            getattr(shift, "amount_default", None)
            or _calc_default_amount(
                base_rate=base_rate,
                extra_hours=extra_hours,
                extra_hour_rate=extra_rate,
                overtime_hours=overtime_hours,
                overtime_hour_rate=overtime_rate,
            )
        )

        shift.base_rate = base_rate
        shift.extra_hours = extra_hours
        shift.overtime_hours = overtime_hours
        shift.amount_default = amount_default
        shift.amount_submitted = amount
        shift.comment = comment
        shift.ended_at = utc_now()

        if amount != amount_default:
            shift.approval_required = True
            shift.status = ShiftInstanceStatus.PENDING_APPROVAL
        else:
            shift.approval_required = False
            shift.status = ShiftInstanceStatus.APPROVED
            shift.amount_approved = amount
            schedule_shift_rating_request_after_commit(session=session, shift_id=int(shift.id))

        await session.flush()
        await _log_event(session, shift_id=int(shift.id), actor_user_id=int(user.id), type="Смена закрыта")
        if amount != amount_default:
            await _log_event(
                session,
                shift_id=int(shift.id),
                actor_user_id=int(user.id),
                type="Сумма изменена сотрудником",
                payload={
                    "default": amount_default,
                    "submitted": amount,
                    "extra_hours": extra_hours,
                    "overtime_hours": overtime_hours,
                },
            )
        else:
            await _log_event(session, shift_id=int(shift.id), actor_user_id=int(user.id), type="Сумма подтверждена руководителем")

        interval = _fmt_plan_interval(day=getattr(shift, "day", None), start_time=start_time, end_time=end_time, is_emergency=is_emergency)
        staff_name = _user_name(user)
        planned_hours = int(getattr(shift, "planned_hours", 0) or 0)
        eh0 = int(extra_hours or 0)
        ov0 = int(overtime_hours or 0)
        fact_hours = planned_hours + eh0 + ov0
        parts: list[str] = []
        parts.append(f"🛑 Смена закрыта: {esc(staff_name)} • {esc(interval)}")
        if fact_hours > 0:
            tail = f"факт: {int(fact_hours)} ч"
            if eh0:
                tail += f" (+{int(eh0)} ч доп)"
            if ov0:
                tail += f" (+{int(ov0)} ч вне графика)"
            parts.append(tail)
        notify_text = " • ".join(parts) if len(parts) == 1 else (parts[0] + " • " + parts[1])

        async def _after_commit() -> None:
            await _notify_admins_and_managers_about_shift_event(
                bot=cb.bot,
                actor_user_id=int(user.id),
                text=str(notify_text),
            )

        add_after_commit_callback(session, _after_commit)

    await state.clear()
    try:
        await edit_html(cb, "Ок, пропускаю.")
    except Exception:
        pass
    if cb.message:
        await send_new_and_delete_active(message=cb.message, state=state, text="Готово.", reply_markup=_kb_schedule_return())


@router.message(ShiftCloseEditState.comment)
async def shift_close_edit_comment(message: Message, state: FSMContext):
    user = await ensure_registered_or_reply(message)
    if not user:
        return

    data = await state.get_data()
    shift_id = int(data.get("shift_id") or 0)
    page = int(data.get("pending_page") or 0)
    extra_hours = int(data.get("extra_hours") or 0)
    overtime_hours = int(data.get("overtime_hours") or 0)
    amount = int(data.get("amount") or 0)
    comment_raw = str(message.text or "").strip()
    comment = None if (not comment_raw or comment_raw == "-") else comment_raw

    async with get_async_session() as session:
        shift = (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.id == int(shift_id))
                .where(ShiftInstance.user_id == int(user.id))
            )
        ).scalar_one_or_none()
        if shift is None:
            try:
                await message.delete()
            except Exception:
                pass
            await send_new_and_delete_active(message=message, state=state, text="Смена не найдена.", reply_markup=_kb_schedule_return())
            await state.clear()
            return

        base_rate = int(getattr(shift, "base_rate", None) or int(getattr(user, "rate_k", 0) or 0))
        extra_rate = int(getattr(shift, "extra_hour_rate", 300) or 300)
        overtime_rate = int(getattr(shift, "overtime_hour_rate", 400) or 400)
        amount_default = int(
            getattr(shift, "amount_default", None)
            or _calc_default_amount(
                base_rate=base_rate,
                extra_hours=extra_hours,
                extra_hour_rate=extra_rate,
                overtime_hours=overtime_hours,
                overtime_hour_rate=overtime_rate,
            )
        )

        shift.base_rate = base_rate
        shift.extra_hours = extra_hours
        shift.overtime_hours = overtime_hours
        shift.amount_default = amount_default
        shift.amount_submitted = amount
        shift.comment = comment
        shift.ended_at = utc_now()

        if amount != amount_default:
            shift.approval_required = True
            shift.status = ShiftInstanceStatus.PENDING_APPROVAL
        else:
            shift.approval_required = False
            shift.status = ShiftInstanceStatus.APPROVED
            shift.amount_approved = amount
            schedule_shift_rating_request_after_commit(session=session, shift_id=int(shift.id))

        await session.flush()
        await _log_event(session, shift_id=int(shift.id), actor_user_id=int(user.id), type="Смена закрыта")
        if amount != amount_default:
            await _log_event(
                session,
                shift_id=int(shift.id),
                actor_user_id=int(user.id),
                type="Сумма изменена сотрудником",
                payload={
                    "default": amount_default,
                    "submitted": amount,
                    "extra_hours": extra_hours,
                    "overtime_hours": overtime_hours,
                },
            )
        else:
            await _log_event(session, shift_id=int(shift.id), actor_user_id=int(user.id), type="Сумма подтверждена руководителем")

        # Notify managers/admins if pending approval
        if shift.status == ShiftInstanceStatus.PENDING_APPROVAL:
            r = role_flags(
                tg_id=int(user.tg_id),
                admin_ids=settings.admin_ids,
                status=user.status,
                position=user.position,
            )
            is_admin = bool(r.is_admin)
            is_manager = bool(r.is_manager)

            # build schedule link for staff
            url = await build_schedule_magic_link(
                session=session,
                user=user,
                is_admin=is_admin,
                is_manager=is_manager,
                ttl_minutes=int(getattr(settings, "JWT_TTL_MINUTES", None) or 60),
            )
            link_text = format_plain_url("📅 График работы", url)

            # recipients
            recipients: set[int] = set(int(x) for x in settings.admin_ids)
            res_m = await session.execute(
                select(User.tg_id)
                .where(User.status == UserStatus.APPROVED)
                .where(User.position == Position.MANAGER)
            )
            for (tg_id,) in res_m.all():
                if tg_id:
                    recipients.add(int(tg_id))

            txt = (
                f"🧾 <b>Смена на подтверждении</b>\n\n"
                f"Сотрудник: <b>{esc((user.first_name or '') + ' ' + (user.last_name or '')).strip() or esc(str(user.tg_id))}</b>\n"
                f"Дата: <b>{shift.day}</b>\n"
                f"Сумма по умолчанию: <b>{amount_default} ₽</b>\n"
                f"Указал сотрудник: <b>{amount} ₽</b>\n"
                f"Доп.часы в рамках смены: <b>{extra_hours} ч</b>\n"
                f"Сверх графика: <b>{overtime_hours} ч</b>\n"
                + (f"Комментарий: {esc(comment)}\n" if comment else "")
                + f"\n{link_text}"
            )

            kb = _kb_pending_item(shift_id=int(shift.id), page=0)
            for chat_id in sorted(recipients):
                try:
                    await message.bot.send_message(chat_id=chat_id, text=txt, reply_markup=kb)
                except Exception:
                    _logger.exception("failed to notify manager", extra={"chat_id": chat_id})

    await state.clear()
    if shift.status == ShiftInstanceStatus.PENDING_APPROVAL:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(
            message=message,
            state=state,
            text="Сумма отличается от расчёта. Руководитель проверит и подтвердит — мы уведомим вас.",
            reply_markup=_kb_schedule_return(),
        )
    else:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(
            message=message,
            state=state,
            text=f"✅ Смена закрыта. Итог: <b>{amount} ₽</b>.",
            reply_markup=_kb_schedule_return(),
        )


@router.callback_query(F.data.startswith("sched_pending:page:"))
async def sched_pending_page(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    actor = await ensure_registered_or_reply(cb)
    if not actor:
        return
    if not await _manager_can(actor, cb.from_user.id):
        await edit_html(cb, "⛔ Нет доступа.")
        return

    try:
        page = int(str(cb.data).split(":", 2)[2])
    except Exception:
        page = 0

    async with get_async_session() as session:
        text, kb = await _render_pending_page(session=session, page=page)
    await state.clear()
    await edit_html(cb, text, reply_markup=kb)


async def _manager_can(user: User, tg_id: int) -> bool:
    r = role_flags(tg_id=int(tg_id), admin_ids=settings.admin_ids, status=user.status, position=user.position)
    return bool(r.is_admin or r.is_manager)


@router.callback_query(F.data.startswith("sched_pending:approve:"))
async def mgr_approve(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    actor = await ensure_registered_or_reply(cb)
    if not actor:
        return
    if not await _manager_can(actor, cb.from_user.id):
        await edit_html(cb, "⛔ Нет доступа.")
        return

    parts = str(cb.data).split(":", 3)
    shift_id = int(parts[2]) if len(parts) >= 3 else 0
    page = int(parts[3]) if len(parts) == 4 else 0
    async with get_async_session() as session:
        shift = (
            await session.execute(select(ShiftInstance).where(ShiftInstance.id == int(shift_id)))
        ).scalar_one_or_none()
        if shift is None:
            await edit_html(cb, "Смена не найдена.")
            return

        shift.amount_approved = int(getattr(shift, "amount_submitted", 0) or 0)
        shift.status = ShiftInstanceStatus.APPROVED
        shift.approval_required = False
        shift.approved_by_user_id = int(actor.id)
        shift.approved_at = utc_now()
        await session.flush()
        schedule_shift_rating_request_after_commit(session=session, shift_id=int(shift.id))
        await _log_event(session, shift_id=int(shift.id), actor_user_id=int(actor.id), type="Сумма подтверждена руководителем")

        staff = (
            await session.execute(select(User).where(User.id == int(shift.user_id)))
        ).scalar_one_or_none()

    async with get_async_session() as session:
        text, kb = await _render_pending_page(session=session, page=page)
    await state.clear()
    await edit_html(cb, f"✅ Подтверждено.\n\n" + text, reply_markup=kb)
    if staff and getattr(staff, "tg_id", None):
        try:
            await cb.bot.send_message(
                chat_id=int(staff.tg_id),
                text=f"✅ Ваша смена за {shift.day} подтверждена. Итог: <b>{int(shift.amount_approved or 0)} ₽</b>.",
            )
        except Exception:
            _logger.exception("failed to notify staff")


@router.callback_query(F.data.startswith("sched_pending:edit:"))
async def mgr_edit(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    actor = await ensure_registered_or_reply(cb)
    if not actor:
        return
    if not await _manager_can(actor, cb.from_user.id):
        await edit_html(cb, "⛔ Нет доступа.")
        return

    parts = str(cb.data).split(":", 3)
    shift_id = int(parts[2]) if len(parts) >= 3 else 0
    page = int(parts[3]) if len(parts) == 4 else 0
    await state.clear()
    await state.set_state(ShiftManagerEditState.amount)
    await state.update_data(
        shift_id=shift_id,
        pending_page=page,
        active_bot_chat_id=int(cb.message.chat.id) if cb.message else None,
        active_bot_message_id=int(cb.message.message_id) if cb.message else None,
    )
    await edit_html(cb, "Введите финальную сумму (₽):", reply_markup=_kb_cancel())


@router.message(ShiftManagerEditState.amount)
async def mgr_edit_amount(message: Message, state: FSMContext):
    actor = await ensure_registered_or_reply(message)
    if not actor:
        return

    data = await state.get_data()
    shift_id = int(data.get("shift_id") or 0)

    try:
        amt = int(str(message.text or "").strip())
        if amt < 0:
            raise ValueError
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(message=message, state=state, text="Введите сумму числом (>=0).", reply_markup=_kb_cancel())
        return

    await state.update_data(amount=amt)
    await state.set_state(ShiftManagerEditState.comment)
    try:
        await message.delete()
    except Exception:
        pass
    await send_new_and_delete_active(
        message=message,
        state=state,
        text="Комментарий (необязательно). Можно пропустить кнопкой ниже.",
        reply_markup=_kb_cancel_skip(skip_data="mgr:comment_skip"),
    )


@router.callback_query(F.data == "mgr:comment_skip")
async def mgr_edit_comment_skip(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    actor = await ensure_registered_or_reply(cb)
    if not actor:
        return

    if not await _manager_can(actor, cb.from_user.id):
        await edit_html(cb, "⛔ Нет доступа.")
        return

    data = await state.get_data()
    shift_id = int(data.get("shift_id") or 0)
    amt = int(data.get("amount") or 0)
    page = int(data.get("pending_page") or 0)
    comment = None

    async with get_async_session() as session:
        shift = (
            await session.execute(select(ShiftInstance).where(ShiftInstance.id == int(shift_id)))
        ).scalar_one_or_none()
        if shift is None:
            await state.clear()
            await edit_html(cb, "Смена не найдена.")
            return

        shift.amount_approved = amt
        shift.status = ShiftInstanceStatus.APPROVED
        shift.approval_required = False
        shift.approved_by_user_id = int(actor.id)
        shift.approved_at = utc_now()
        await session.flush()
        schedule_shift_rating_request_after_commit(session=session, shift_id=int(shift.id))
        await _log_event(
            session,
            shift_id=int(shift.id),
            actor_user_id=int(actor.id),
            type="Сумма изменена руководителем",
            payload={"amount_approved": amt, "comment": comment},
        )

    await state.clear()
    async with get_async_session() as session:
        text, kb = await _render_pending_page(session=session, page=page)
    if cb.message:
        await send_new_and_delete_active(
            message=cb.message,
            state=state,
            text=f"✅ Подтверждено. Итог: <b>{amt} ₽</b>.\n\n" + text,
            reply_markup=kb,
        )


@router.message(ShiftManagerEditState.comment)
async def mgr_edit_comment(message: Message, state: FSMContext):
    actor = await ensure_registered_or_reply(message)
    if not actor:
        return

    if not await _manager_can(actor, message.from_user.id):
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(message=message, state=state, text="⛔ Нет доступа.")
        await state.clear()
        return

    data = await state.get_data()
    shift_id = int(data.get("shift_id") or 0)
    amt = int(data.get("amount") or 0)
    page = int(data.get("pending_page") or 0)
    comment_raw = str(message.text or "").strip()
    comment = None if (not comment_raw or comment_raw == "-") else comment_raw

    async with get_async_session() as session:
        shift = (
            await session.execute(select(ShiftInstance).where(ShiftInstance.id == int(shift_id)))
        ).scalar_one_or_none()
        if shift is None:
            try:
                await message.delete()
            except Exception:
                pass
            await send_new_and_delete_active(message=message, state=state, text="Смена не найдена.")
            await state.clear()
            return

        shift.amount_approved = amt
        shift.status = ShiftInstanceStatus.APPROVED
        shift.approval_required = False
        shift.approved_by_user_id = int(actor.id)
        shift.approved_at = utc_now()
        await session.flush()
        await _log_event(
            session,
            shift_id=int(shift.id),
            actor_user_id=int(actor.id),
            type="Сумма изменена руководителем",
            payload={"amount_approved": amt, "comment": comment},
        )

        staff = (
            await session.execute(select(User).where(User.id == int(shift.user_id)))
        ).scalar_one_or_none()

    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    async with get_async_session() as session:
        text, kb = await _render_pending_page(session=session, page=page)
    await send_new_and_delete_active(message=message, state=state, text=f"✅ Подтверждено. Итог: <b>{amt} ₽</b>.\n\n" + text, reply_markup=kb)
    if staff and getattr(staff, "tg_id", None):
        try:
            await message.bot.send_message(
                chat_id=int(staff.tg_id),
                text=f"✅ Руководитель утвердил сумму за смену {shift.day}: <b>{amt} ₽</b>.",
            )
        except Exception:
            _logger.exception("failed to notify staff")


@router.callback_query(F.data.startswith("sched_pending:rework:"))
async def mgr_rework(cb: CallbackQuery, state: FSMContext):
    await _cb_answer_safely(cb)
    actor = await ensure_registered_or_reply(cb)
    if not actor:
        return
    if not await _manager_can(actor, cb.from_user.id):
        await edit_html(cb, "⛔ Нет доступа.")
        return

    parts = str(cb.data).split(":", 3)
    shift_id = int(parts[2]) if len(parts) >= 3 else 0
    page = int(parts[3]) if len(parts) == 4 else 0
    await state.clear()
    await state.set_state(ShiftManagerEditState.comment)
    await state.update_data(
        shift_id=shift_id,
        rework=True,
        pending_page=page,
        active_bot_chat_id=int(cb.message.chat.id) if cb.message else None,
        active_bot_message_id=int(cb.message.message_id) if cb.message else None,
    )
    await edit_html(cb, "Комментарий обязателен (почему на доработку):", reply_markup=_kb_cancel())


@router.message(ShiftManagerEditState.comment)
async def mgr_rework_comment(message: Message, state: FSMContext):
    actor = await ensure_registered_or_reply(message)
    if not actor:
        return

    data = await state.get_data()
    if not data.get("rework"):
        return

    if not await _manager_can(actor, message.from_user.id):
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(message=message, state=state, text="⛔ Нет доступа.")
        await state.clear()
        return

    shift_id = int(data.get("shift_id") or 0)
    comment = str(message.text or "").strip()
    if not comment:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(message=message, state=state, text="Комментарий обязателен.", reply_markup=_kb_cancel())
        return

    page = int(data.get("pending_page") or 0)

    async with get_async_session() as session:
        shift = (
            await session.execute(select(ShiftInstance).where(ShiftInstance.id == int(shift_id)))
        ).scalar_one_or_none()
        if shift is None:
            try:
                await message.delete()
            except Exception:
                pass
            await send_new_and_delete_active(message=message, state=state, text="Смена не найдена.")
            await state.clear()
            return

        shift.status = ShiftInstanceStatus.NEEDS_REWORK
        shift.approval_required = True
        shift.comment = comment
        await session.flush()
        await _log_event(session, shift_id=int(shift.id), actor_user_id=int(actor.id), type="Отправлено на доработку", payload={"comment": comment})

        staff = (
            await session.execute(select(User).where(User.id == int(shift.user_id)))
        ).scalar_one_or_none()

    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass
    async with get_async_session() as session:
        text, kb = await _render_pending_page(session=session, page=page)
    await send_new_and_delete_active(message=message, state=state, text="🔁 Отправлено на доработку.\n\n" + text, reply_markup=kb)
    if staff and getattr(staff, "tg_id", None):
        try:
            await message.bot.send_message(
                chat_id=int(staff.tg_id),
                text=f"🔁 Смена {shift.day} отправлена на доработку.\nКомментарий: {esc(comment)}",
            )
        except Exception:
            _logger.exception("failed to notify staff")
