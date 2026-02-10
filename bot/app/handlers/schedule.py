from __future__ import annotations

import logging
from datetime import datetime
from datetime import time as dtime

from aiogram import Router, F
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from shared.config import settings
from shared.db import get_async_session
from shared.db import add_after_commit_callback
from shared.enums import UserStatus
from shared.permissions import role_flags
from shared.permissions import can_access_shifts
from sqlalchemy import select

from bot.app.guards.user_guard import ensure_registered_or_reply
from bot.app.keyboards.main import main_menu_kb
from bot.app.utils.urls import build_schedule_magic_link
from bot.app.utils.telegram import edit_html, send_html, send_new_and_delete_active
from bot.app.utils.html import format_plain_url
from bot.app.states.schedule import ScheduleEmergencyState
from shared.models import WorkShiftDay, User, ShiftInstance
from shared.enums import ShiftInstanceStatus
from shared.utils import MOSCOW_TZ
from shared.utils import utc_now

from shared.services.shifts_domain import (
    calc_int_hours_from_times,
    format_hours_from_times_int,
    is_shift_active_status,
    is_shift_final_status,
    normalize_shift_times,
)


router = Router()
_logger = logging.getLogger(__name__)


async def _notify_shift_if_due_after_commit(*, user_id: int, day, start_time: dtime, end_time: dtime) -> None:
    try:
        now_msk = datetime.now(MOSCOW_TZ)
        if day != now_msk.date():
            return

        start_dt = datetime.combine(day, start_time, tzinfo=MOSCOW_TZ)
        end_dt = datetime.combine(day, end_time, tzinfo=MOSCOW_TZ)

        async with get_async_session() as session:
            u = (
                await session.execute(select(User).where(User.id == int(user_id)).where(User.is_deleted == False))
            ).scalar_one_or_none()
            if u is None:
                return
            chat_id = int(getattr(u, "tg_id", 0) or 0)
            if not chat_id:
                return

            wsd = (
                await session.execute(
                    select(WorkShiftDay)
                    .where(WorkShiftDay.user_id == int(user_id))
                    .where(WorkShiftDay.day == day)
                    .where(WorkShiftDay.kind == "work")
                )
            ).scalar_one_or_none()
            if wsd is None:
                return

            bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
            try:
                iso_day = str(day)

                if now_msk >= end_dt:
                    if getattr(wsd, "end_notified_at", None) is not None:
                        return
                    text = (
                        f"🏁 <b>Смена по графику закончилась</b>\n\n"
                        f"Конец по графику: <b>{end_time.strftime('%H:%M')}</b>.\n"
                        f"Завершить смену?"
                    )
                    kb = {
                        "inline_keyboard": [
                            [{"text": "✅ Завершить", "callback_data": f"shift:close_by_day:{iso_day}"}],
                            [{"text": "⏰ Ещё работаю", "callback_data": f"shift:end_snooze:{iso_day}"}],
                            [{"text": "📅 Меню графика", "callback_data": "sched_menu:open"}],
                        ]
                    }
                    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
                    wsd.end_notified_at = utc_now()
                    wsd.end_snooze_until = None
                    wsd.end_followup_notified_at = None
                    await session.flush()
                    return

                if now_msk >= start_dt:
                    if getattr(wsd, "start_notified_at", None) is not None:
                        return
                    hrs = format_hours_from_times_int(start_time=start_time, end_time=end_time)
                    text = (
                        f"⏰ <b>Начало смены</b>\n\n"
                        f"Сегодня у тебя смена: <b>{start_time.strftime('%H:%M')}–{end_time.strftime('%H:%M')}</b> ({hrs} часов).\n"
                        f"Начать смену?"
                    )
                    kb = {
                        "inline_keyboard": [
                            [{"text": "✅ Начать", "callback_data": f"shift:start:{iso_day}"}],
                            [{"text": "📅 Меню графика", "callback_data": "sched_menu:open"}],
                        ]
                    }
                    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
                    wsd.start_notified_at = utc_now()
                    await session.flush()
            finally:
                await bot.session.close()
    except Exception:
        _logger.exception("failed to send immediate shift notification")


def _kb_cancel_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="sched_em_cancel")]])


def _kb_emergency_time_quick() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="10:00–18:00", callback_data="sched_em_t:10:00-18:00"),
            InlineKeyboardButton(text="10:00–20:00", callback_data="sched_em_t:10:00-20:00"),
            InlineKeyboardButton(text="10:00–22:00", callback_data="sched_em_t:10:00-22:00"),
        ],
        [InlineKeyboardButton(text="Своё время", callback_data="sched_em_t:custom")],
        [InlineKeyboardButton(text="Отмена", callback_data="sched_em_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _format_interval_for_confirm(*, st: dtime, et: dtime) -> str:
    hrs = format_hours_from_times_int(start_time=st, end_time=et)
    return f"{st.strftime('%H:%M')}–{et.strftime('%H:%M')} ({hrs}ч)"


def _parse_time_range_hhmm(txt: str) -> tuple[dtime, dtime] | None:
    s = str(txt or "").strip()
    import re

    m = re.match(r"^\s*([0-9]{1,2}:[0-9]{2})\s*[-–—]\s*([0-9]{1,2}:[0-9]{2})\s*$", s)
    if not m:
        return None
    try:
        from datetime import datetime as _dt

        st = _dt.strptime(m.group(1), "%H:%M").time()
        et = _dt.strptime(m.group(2), "%H:%M").time()
        return st, et
    except Exception:
        return None


def _kb_emergency_date_mode() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Сегодня", callback_data="sched_em_date:today")],
        [InlineKeyboardButton(text="Выбрать дату", callback_data="sched_em_date:pick")],
        [InlineKeyboardButton(text="Отмена", callback_data="sched_em_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_yes_no(*, yes_data: str, no_data: str, yes_text: str = "Да", no_text: str = "Нет") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=yes_text, callback_data=yes_data), InlineKeyboardButton(text=no_text, callback_data=no_data)],
        ]
    )


def _kb_emergency_comment() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="sched_em_comment:skip")],
            [InlineKeyboardButton(text="Отмена", callback_data="sched_em_cancel")],
        ]
    )


def _kb_emergency_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать", callback_data="sched_em_confirm"), InlineKeyboardButton(text="Отмена", callback_data="sched_em_cancel")],
        ]
    )


def _format_user_name(u: User) -> str:
    name = " ".join([str(getattr(u, "first_name", "") or "").strip(), str(getattr(u, "last_name", "") or "").strip()]).strip()
    if name:
        return name
    username = str(getattr(u, "username", "") or "").strip()
    if username:
        return username
    return f"User #{int(getattr(u, 'id'))}"


async def _kb_pick_user(*, session, page: int = 0, page_size: int = 10) -> InlineKeyboardMarkup:
    p = max(0, int(page))
    size = max(5, min(20, int(page_size)))
    res = await session.execute(
        select(User)
        .where(User.is_deleted == False)
        .where(User.status == UserStatus.APPROVED)
        .order_by(User.first_name, User.last_name, User.id)
        .offset(p * size)
        .limit(size)
    )
    users = list(res.scalars().all())

    rows: list[list[InlineKeyboardButton]] = []
    for u in users:
        rows.append([InlineKeyboardButton(text=_format_user_name(u), callback_data=f"sched_em_user:{int(getattr(u,'id'))}")])

    nav: list[InlineKeyboardButton] = []
    if p > 0:
        nav.append(InlineKeyboardButton(text="←", callback_data=f"sched_em_user_page:{p-1}"))
    if len(users) == size:
        nav.append(InlineKeyboardButton(text="→", callback_data=f"sched_em_user_page:{p+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="Отмена", callback_data="sched_em_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(F.text.in_({"📅 График работы", "График работы"}))
async def schedule_entry(message: Message, state: FSMContext):
    user = await ensure_registered_or_reply(message)
    if not user:
        return

    if user.status == UserStatus.BLACKLISTED:
        await message.answer(
            "🚫 Доступ ограничен.",
            reply_markup=main_menu_kb(None, message.from_user.id),
        )
        return

    r = role_flags(
        tg_id=int(message.from_user.id),
        admin_ids=settings.admin_ids,
        status=user.status,
        position=user.position,
    )
    if not can_access_shifts(r=r, status=user.status):
        await message.answer(
            "Недоступно для вашей должности.",
            reply_markup=main_menu_kb(user.status, message.from_user.id, user.position),
        )
        return
    if not (user.status == UserStatus.APPROVED or (bool(r.is_admin) or bool(r.is_manager))):
        await message.answer(
            "⏳ Раздел «График работы» доступен только одобренным сотрудникам.",
            reply_markup=main_menu_kb(user.status, message.from_user.id, user.position),
        )
        return

    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    async with get_async_session() as session:
        text, kb = await _render_schedule_menu(session=session, user=user, is_admin=is_admin, is_manager=is_manager)
    await send_new_and_delete_active(message=message, state=state, text=text, reply_markup=kb)


 

@router.callback_query(F.data.in_({"sched_menu:open", "sched_menu:refresh"}))
async def schedule_menu_open(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    if user.status == UserStatus.BLACKLISTED:
        await edit_html(cb, "🚫 Доступ ограничен.")
        return

    r = role_flags(
        tg_id=int(cb.from_user.id),
        admin_ids=settings.admin_ids,
        status=user.status,
        position=user.position,
    )
    if not (user.status == UserStatus.APPROVED or (bool(r.is_admin) or bool(r.is_manager))):
        await edit_html(cb, "⏳ Раздел «График работы» доступен только одобренным сотрудникам.")
        return

    async with get_async_session() as session:
        text, kb = await _render_schedule_menu(session=session, user=user, is_admin=bool(r.is_admin), is_manager=bool(r.is_manager))
    if cb.message:
        await send_new_and_delete_active(message=cb.message, state=state, text=text, reply_markup=kb)
    else:
        await edit_html(cb, text, reply_markup=kb)


def _ru_shift_status(s: str | None) -> str:
    m = {
        "planned": "Запланировано",
        "started": "Открыта",
        "closed": "Закрыта",
        "pending_approval": "На подтверждении",
        "approved": "Подтверждена",
        "rejected": "Отклонена",
        "needs_rework": "На доработку",
    }
    return m.get(str(s or ""), "—")


async def _render_schedule_menu(*, session, user: User, is_admin: bool, is_manager: bool):
    try:
        today = datetime.now(MOSCOW_TZ).date()
        now_msk = datetime.now(MOSCOW_TZ)
        plan = (
            await session.execute(
                select(WorkShiftDay)
                .where(WorkShiftDay.user_id == int(user.id))
                .where(WorkShiftDay.day == today)
            )
        ).scalar_one_or_none()

        shift = (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.user_id == int(user.id))
                .where(ShiftInstance.day == today)
                .order_by(ShiftInstance.id.desc())
            )
        ).scalar_one_or_none()

        has_plan_work = bool(plan is not None and str(getattr(plan, "kind", "")) == "work")
        if has_plan_work:
            st0 = getattr(plan, "start_time", None) or dtime(10, 0)
            et0 = getattr(plan, "end_time", None) or dtime(18, 0)
            hrs0 = format_hours_from_times_int(start_time=st0, end_time=et0)
            plan_txt = f"{st0.strftime('%H:%M')}–{et0.strftime('%H:%M')} ({hrs0}ч)"
            if bool(getattr(plan, "is_emergency", False)):
                plan_txt += " ⚡"
        else:
            plan_txt = "нет смены"

        shift_status = str(getattr(shift, "status", "") or "") if shift is not None else ""
        is_finished = bool(
            shift is not None
            and is_shift_final_status(getattr(shift, "status", None), ended_at=getattr(shift, "ended_at", None))
        )

        status_txt = "нет данных"
        if shift is None:
            status_txt = "нет смены"
        elif is_finished:
            started_at = getattr(shift, "started_at", None)
            ended_at = getattr(shift, "ended_at", None)
            if started_at is not None and ended_at is not None:
                try:
                    s0 = started_at.astimezone(MOSCOW_TZ).strftime("%H:%M")
                    e0 = ended_at.astimezone(MOSCOW_TZ).strftime("%H:%M")
                    status_txt = f"Завершена ✅ ({s0}–{e0})"
                except Exception:
                    status_txt = "Завершена ✅"
            else:
                status_txt = "Завершена ✅"
        else:
            # Use the actual shift status; this must exist in all branches
            status_txt = _ru_shift_status(shift_status) if shift_status else "нет данных"

        amount = None
        if shift is not None:
            amount = getattr(shift, "amount_approved", None)
            if amount is None:
                amount = getattr(shift, "amount_submitted", None)
            if amount is None:
                amount = getattr(shift, "amount_default", None)

        amount_txt = f"{int(amount)} ₽" if amount is not None else "—"

        url = await build_schedule_magic_link(
            session=session,
            user=user,
            is_admin=is_admin,
            is_manager=is_manager,
            ttl_minutes=int(getattr(settings, "JWT_TTL_MINUTES", None) or 60),
        )

        text = (
            f"<b>График работы</b>\n\n"
            f"Сегодня: <b>{plan_txt}</b>\n"
            f"Факт: <b>{status_txt}</b>\n"
            f"Сумма: <b>{amount_txt}</b>\n\n"
            f"Открыть календарь:\n{url}\n"
        )

        # Buttons (minimal, contextual)
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        rows: list[list[InlineKeyboardButton]] = []

        # If active shift -> only finish
        if shift is not None and shift_status == "started":
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ Завершить смену",
                        callback_data=f"sch:finish:{int(getattr(shift, 'id'))}",
                    )
                ]
            )
        else:
            # No active shift
            if is_finished:
                pass
            elif has_plan_work:
                st0 = getattr(plan, "start_time", None) or dtime(10, 0)
                start_dt = datetime.combine(today, st0, tzinfo=MOSCOW_TZ)
                if datetime.now(MOSCOW_TZ) >= start_dt:
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text="✅ Начать смену",
                                callback_data=f"shift:start:{today.isoformat()}",
                            )
                        ]
                    )
            else:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="⚡ Начать экстренную смену",
                            callback_data="sched_em_start",
                        )
                    ]
                )

        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        return text, kb
    except Exception:
        _logger.exception("failed to render schedule menu")

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        url = "—"
        try:
            url = await build_schedule_magic_link(
                session=session,
                user=user,
                is_admin=is_admin,
                is_manager=is_manager,
                ttl_minutes=int(getattr(settings, "JWT_TTL_MINUTES", None) or 60),
            )
        except Exception:
            _logger.exception("failed to build schedule magic link")

        text = (
            f"<b>График работы</b>\n\n"
            f"Сегодня: <b>нет данных</b>\n"
            f"Факт: <b>нет данных</b>\n\n"
            f"Открыть календарь:\n{url}\n"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⚡ Начать экстренную смену", callback_data="sched_em_start")]]
        )
        return text, kb


@router.callback_query(F.data == "sched_em_start")
async def schedule_emergency_start(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    user = await ensure_registered_or_reply(cb)
    if not user:
        return
    if user.status == UserStatus.BLACKLISTED:
        await edit_html(cb, "🚫 Доступ ограничен.")
        return
    r = role_flags(
        tg_id=int(cb.from_user.id),
        admin_ids=settings.admin_ids,
        status=user.status,
        position=user.position,
    )
    if not (user.status == UserStatus.APPROVED or (bool(r.is_admin) or bool(r.is_manager))):
        await edit_html(cb, "⏳ Раздел «График работы» доступен только одобренным сотрудникам.")
        return
    await state.clear()
    await state.update_data(
        actor_tg_id=int(cb.from_user.id),
        is_admin=bool(r.is_admin),
        is_manager=bool(r.is_manager),
        target_user_id=int(user.id),
    )
    # Default: 10:00–18:00
    await state.update_data(start_time="10:00", end_time="18:00")
    await state.set_state(ScheduleEmergencyState.pick_time)
    await edit_html(cb, "Выберите время экстренной смены:", reply_markup=_kb_emergency_time_quick())


@router.callback_query(F.data == "sched_em_cancel")
async def schedule_emergency_cancel(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    await state.clear()
    try:
        await edit_html(cb, "Отменено.")
    except Exception:
        try:
            await cb.message.answer("Отменено.")
        except Exception:
            pass


@router.callback_query(F.data.startswith("sched_em_t:"))
async def schedule_emergency_pick_time(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    data = str(cb.data or "")
    payload = data.split(":", 1)[1] if ":" in data else ""
    if payload == "custom":
        await state.set_state(ScheduleEmergencyState.input_time)
        await edit_html(
            cb,
            "Введите время в формате HH:MM-HH:MM (например 10:00-18:00):",
            reply_markup=_kb_cancel_inline(),
        )
        return

    parsed = _parse_time_range_hhmm(payload)
    if parsed is None:
        await edit_html(cb, "Не удалось распознать время.")
        return
    st0, et0 = parsed
    try:
        st_n, et_n = normalize_shift_times(kind="work", start_time=st0, end_time=et0)
        if st_n is None or et_n is None:
            raise ValueError("invalid")
        h_int = calc_int_hours_from_times(start_time=st_n, end_time=et_n)
        if h_int is None:
            await edit_html(cb, "Можно только целые часы. Выберите другое время.")
            return
    except ValueError:
        await edit_html(cb, "Конец должен быть позже начала.")
        return

    st = await state.get_data()
    is_admin_or_manager = bool(st.get("is_admin") or st.get("is_manager"))
    await state.update_data(start_time=st_n.strftime("%H:%M"), end_time=et_n.strftime("%H:%M"), hours=int(h_int))

    # For regular user flow started from menu: emergency shift is for today.
    if not is_admin_or_manager:
        today = datetime.now(MOSCOW_TZ).date().isoformat()
        await state.update_data(day=today, target_user_id=int(st.get("target_user_id") or 0))
        await state.set_state(ScheduleEmergencyState.input_comment)
        await edit_html(
            cb,
            "Комментарий (опционально). Можете написать сообщением или пропустить:",
            reply_markup=_kb_emergency_comment(),
        )
        return

    if is_admin_or_manager:
        await state.set_state(ScheduleEmergencyState.pick_date_mode)
        async with get_async_session() as session:
            kb = await _kb_pick_user(session=session, page=0)
        await edit_html(cb, "Для кого открыть экстренную смену?", reply_markup=kb)
        return

    await state.set_state(ScheduleEmergencyState.pick_date_mode)
    await edit_html(cb, "На какую дату открыть смену?", reply_markup=_kb_emergency_date_mode())


@router.message(ScheduleEmergencyState.input_time)
async def schedule_emergency_input_time(message: Message, state: FSMContext):
    txt = str(message.text or "").strip()
    parsed = _parse_time_range_hhmm(txt)
    if parsed is None:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(
            message=message,
            state=state,
            text="Неверный формат. Введите HH:MM-HH:MM (например 10:00-18:00):",
            reply_markup=_kb_cancel_inline(),
        )
        return
    st0, et0 = parsed
    try:
        st_n, et_n = normalize_shift_times(kind="work", start_time=st0, end_time=et0)
        if st_n is None or et_n is None:
            raise ValueError("invalid")
        h_int = calc_int_hours_from_times(start_time=st_n, end_time=et_n)
        if h_int is None:
            raise ValueError("non_int")
    except ValueError as e:
        try:
            await message.delete()
        except Exception:
            pass
        if str(e) == "non_int":
            await send_new_and_delete_active(
                message=message,
                state=state,
                text="Можно только целые часы. Выберите другое время.",
                reply_markup=_kb_cancel_inline(),
            )
        else:
            await send_new_and_delete_active(
                message=message,
                state=state,
                text="Конец должен быть позже начала.",
                reply_markup=_kb_cancel_inline(),
            )
        return

    await state.update_data(start_time=st_n.strftime("%H:%M"), end_time=et_n.strftime("%H:%M"), hours=int(h_int))

    st = await state.get_data()
    is_admin_or_manager = bool(st.get("is_admin") or st.get("is_manager"))
    if not is_admin_or_manager:
        today = datetime.now(MOSCOW_TZ).date().isoformat()
        await state.update_data(day=today, target_user_id=int(st.get("target_user_id") or 0))
        await state.set_state(ScheduleEmergencyState.input_comment)
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(
            message=message,
            state=state,
            text="Комментарий (опционально). Можете написать сообщением или пропустить:",
            reply_markup=_kb_emergency_comment(),
        )
        return

    await state.set_state(ScheduleEmergencyState.pick_date_mode)
    try:
        await message.delete()
    except Exception:
        pass
    async with get_async_session() as session:
        kb = await _kb_pick_user(session=session, page=0)
    await send_new_and_delete_active(message=message, state=state, text="Для кого открыть экстренную смену?", reply_markup=kb)


@router.callback_query(F.data.startswith("sched_em_user_page:"))
async def schedule_emergency_user_page(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    st = await state.get_data()
    is_admin_or_manager = bool(st.get("is_admin") or st.get("is_manager"))
    if not is_admin_or_manager:
        await edit_html(cb, "⛔ Недостаточно прав.")
        return

    try:
        page = int(str(cb.data or "").split(":", 1)[1])
    except Exception:
        page = 0

    async with get_async_session() as session:
        kb = await _kb_pick_user(session=session, page=page)
    await edit_html(cb, "Для кого открыть экстренную смену?", reply_markup=kb)


@router.callback_query(F.data.startswith("sched_em_user:"))
async def schedule_emergency_pick_user(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    st = await state.get_data()
    is_admin_or_manager = bool(st.get("is_admin") or st.get("is_manager"))
    if not is_admin_or_manager:
        await edit_html(cb, "⛔ Недостаточно прав.")
        return

    try:
        uid = int(str(cb.data or "").split(":", 1)[1])
    except Exception:
        await edit_html(cb, "Не удалось распознать пользователя.")
        return

    await state.update_data(target_user_id=uid)
    await state.set_state(ScheduleEmergencyState.pick_date_mode)
    await edit_html(cb, "На какую дату открыть смену?", reply_markup=_kb_emergency_date_mode())


@router.callback_query(F.data.startswith("sched_em_date:"))
async def schedule_emergency_pick_date_mode(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    mode = str(cb.data or "").split(":", 1)[1] if ":" in str(cb.data or "") else ""
    if mode == "today":
        from datetime import date as _date

        d = _date.today().isoformat()
        await state.update_data(day=d)
        await state.set_state(ScheduleEmergencyState.input_comment)
        await edit_html(cb, "Комментарий (опционально). Можете написать сообщением или пропустить:", reply_markup=_kb_emergency_comment())
        return

    if mode == "pick":
        await state.set_state(ScheduleEmergencyState.input_date)
        await edit_html(cb, "Введите дату в формате YYYY-MM-DD (например 2026-01-15):", reply_markup=_kb_cancel_inline())
        return

    await edit_html(cb, "Неизвестный выбор.")


@router.message(ScheduleEmergencyState.input_date)
async def schedule_emergency_input_date(message: Message, state: FSMContext):
    txt = str(message.text or "").strip()
    try:
        from datetime import datetime as _dt

        _dt.strptime(txt, "%Y-%m-%d")
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(message=message, state=state, text="Неверная дата. Введите в формате YYYY-MM-DD.", reply_markup=_kb_cancel_inline())
        return

    await state.update_data(day=txt)
    await state.set_state(ScheduleEmergencyState.input_comment)
    try:
        await message.delete()
    except Exception:
        pass
    await send_new_and_delete_active(
        message=message,
        state=state,
        text="Комментарий (опционально). Можете написать сообщением или пропустить:",
        reply_markup=_kb_emergency_comment(),
    )


@router.callback_query(F.data == "sched_em_comment:skip")
async def schedule_emergency_skip_comment(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    st = await state.get_data()
    day = str(st.get("day") or "").strip()
    st_s = str(st.get("start_time") or "").strip() or "10:00"
    et_s = str(st.get("end_time") or "").strip() or "18:00"
    interval_txt = f"{st_s}–{et_s}"
    await state.update_data(comment="")
    await state.set_state(ScheduleEmergencyState.confirm)
    await edit_html(cb, f"Подтвердите создание экстренной смены:\n\nДата: {day}\nВремя: {interval_txt}", reply_markup=_kb_emergency_confirm())


@router.message(ScheduleEmergencyState.input_comment)
async def schedule_emergency_input_comment(message: Message, state: FSMContext):
    txt = str(message.text or "").strip()
    await state.update_data(comment=txt)
    st = await state.get_data()
    day = str(st.get("day") or "").strip()
    st_s = str(st.get("start_time") or "").strip() or "10:00"
    et_s = str(st.get("end_time") or "").strip() or "18:00"
    interval_txt = f"{st_s}–{et_s}"
    await state.set_state(ScheduleEmergencyState.confirm)
    try:
        await message.delete()
    except Exception:
        pass
    await send_new_and_delete_active(
        message=message,
        state=state,
        text=f"Подтвердите создание экстренной смены:\n\nДата: {day}\nВремя: {interval_txt}" + (f"\nКомментарий: {txt}" if txt else ""),
        reply_markup=_kb_emergency_confirm(),
    )


async def _create_or_replace_emergency(
    *,
    session,
    target_user_id: int,
    day: str,
    start_time: dtime,
    end_time: dtime,
    comment: str | None,
    replace: bool,
) -> tuple[str, bool, dtime, dtime, int]:
    from datetime import datetime as _dt

    d = _dt.strptime(str(day), "%Y-%m-%d").date()

    existing = (
        await session.execute(select(WorkShiftDay).where(WorkShiftDay.user_id == int(target_user_id)).where(WorkShiftDay.day == d))
    ).scalar_one_or_none()

    fact = (
        await session.execute(select(ShiftInstance).where(ShiftInstance.user_id == int(target_user_id)).where(ShiftInstance.day == d))
    ).scalar_one_or_none()

    if fact is not None:
        if is_shift_final_status(getattr(fact, "status", None), ended_at=getattr(fact, "ended_at", None)):
            h0 = calc_int_hours_from_times(start_time=start_time, end_time=end_time) or 0
            return ("У тебя уже есть завершённая смена на эту дату.", True, start_time, end_time, int(h0))
        if is_shift_active_status(getattr(fact, "status", None), ended_at=getattr(fact, "ended_at", None)):
            h0 = calc_int_hours_from_times(start_time=start_time, end_time=end_time) or 0
            return ("Смена уже открыта.", True, start_time, end_time, int(h0))

    st_n, et_n = normalize_shift_times(kind="work", start_time=start_time, end_time=end_time)
    if st_n is None or et_n is None:
        return ("Не задано время смены.", True, start_time, end_time, 0)
    h_int = calc_int_hours_from_times(start_time=st_n, end_time=et_n)
    if h_int is None:
        return ("Можно только целые часы. Выберите другое время.", True, st_n, et_n, 0)
    hours = int(h_int)

    if existing is not None:
        if bool(getattr(existing, "is_emergency", False)):
            existing.kind = "work"
            existing.hours = int(hours)
            existing.comment = comment
            existing.start_time = st_n
            existing.end_time = et_n
            await session.flush()
            add_after_commit_callback(
                session,
                lambda: _notify_shift_if_due_after_commit(user_id=int(target_user_id), day=d, start_time=st_n, end_time=et_n),
            )
            # Start fact if absent
            if fact is None:
                u = (
                    await session.execute(select(User).where(User.id == int(target_user_id)).where(User.is_deleted == False))
                ).scalar_one_or_none()
                base_rate = int(getattr(u, "rate_k", 0) or 0) if u is not None else 0
                si = ShiftInstance(
                    user_id=int(target_user_id),
                    day=d,
                    planned_hours=int(hours),
                    is_emergency=True,
                    started_at=utc_now(),
                    status=ShiftInstanceStatus.STARTED,
                    base_rate=base_rate,
                )
                session.add(si)
                await session.flush()
            return ("Экстренная смена назначена.", True, st_n, et_n, int(hours))

        if not replace:
            return ("Смена уже запланирована. Заменить?", False, st_n, et_n, int(hours))

        existing.kind = "work"
        existing.hours = int(hours)
        existing.is_emergency = True
        existing.comment = comment
        existing.start_time = st_n
        existing.end_time = et_n
        await session.flush()
        add_after_commit_callback(
            session,
            lambda: _notify_shift_if_due_after_commit(user_id=int(target_user_id), day=d, start_time=st_n, end_time=et_n),
        )
        # Start fact if absent
        if fact is None:
            u = (
                await session.execute(select(User).where(User.id == int(target_user_id)).where(User.is_deleted == False))
            ).scalar_one_or_none()
            base_rate = int(getattr(u, "rate_k", 0) or 0) if u is not None else 0
            si = ShiftInstance(
                user_id=int(target_user_id),
                day=d,
                planned_hours=int(hours),
                is_emergency=True,
                started_at=utc_now(),
                status=ShiftInstanceStatus.STARTED,
                base_rate=base_rate,
            )
            session.add(si)
            await session.flush()
        return ("Экстренная смена назначена.", True, st_n, et_n, int(hours))

    row = WorkShiftDay(
        user_id=int(target_user_id),
        day=d,
        kind="work",
        hours=int(hours),
        start_time=st_n,
        end_time=et_n,
        is_emergency=True,
        comment=comment,
    )
    session.add(row)
    await session.flush()
    add_after_commit_callback(
        session,
        lambda: _notify_shift_if_due_after_commit(user_id=int(target_user_id), day=d, start_time=st_n, end_time=et_n),
    )
    # Start fact
    if fact is None:
        u = (
            await session.execute(select(User).where(User.id == int(target_user_id)).where(User.is_deleted == False))
        ).scalar_one_or_none()
        base_rate = int(getattr(u, "rate_k", 0) or 0) if u is not None else 0
        si = ShiftInstance(
            user_id=int(target_user_id),
            day=d,
            planned_hours=int(hours),
            is_emergency=True,
            started_at=utc_now(),
            status=ShiftInstanceStatus.STARTED,
            base_rate=base_rate,
        )
        session.add(si)
        await session.flush()
    return ("Экстренная смена назначена.", True, st_n, et_n, int(hours))


@router.callback_query(F.data == "sched_em_confirm")
async def schedule_emergency_confirm(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    st = await state.get_data()
    day = str(st.get("day") or "").strip()
    st_s = str(st.get("start_time") or "").strip() or "10:00"
    et_s = str(st.get("end_time") or "").strip() or "18:00"
    comment = str(st.get("comment") or "").strip() or None
    target_user_id = int(st.get("target_user_id") or 0)

    parsed = _parse_time_range_hhmm(f"{st_s}-{et_s}")
    if parsed is None:
        await edit_html(cb, "Недостаточно данных для создания смены.")
        await state.clear()
        return
    st0, et0 = parsed
    st_n, et_n = normalize_shift_times(kind="work", start_time=st0, end_time=et0)
    h_int = calc_int_hours_from_times(start_time=st_n, end_time=et_n) if st_n is not None and et_n is not None else None

    if not day or h_int is None or target_user_id <= 0:
        await edit_html(cb, "Недостаточно данных для создания смены.")
        await state.clear()
        return

    async with get_async_session() as session:
        msg, ok, st, et, hours2 = await _create_or_replace_emergency(
            session=session,
            target_user_id=target_user_id,
            day=day,
            start_time=st_n,
            end_time=et_n,
            comment=comment,
            replace=False,
        )
        if not ok:
            await state.update_data(replace_pending=True)
            kb = _kb_yes_no(yes_data="sched_em_replace_yes", no_data="sched_em_cancel", yes_text="Заменить", no_text="Отмена")
            await edit_html(cb, msg, reply_markup=kb)
            return

        # Build link
        actor_tg_id = int(st.get("actor_tg_id") or 0)
        is_admin = bool(st.get("is_admin"))
        is_manager = bool(st.get("is_manager"))
        tok_user = (
            await session.execute(select(User).where(User.id == int(target_user_id)).where(User.is_deleted == False))
        ).scalar_one_or_none()
        if tok_user is None:
            await edit_html(cb, "Смена создана, но не удалось сформировать ссылку.")
            await state.clear()
            return
        url = await build_schedule_magic_link(
            session=session,
            user=tok_user,
            is_admin=is_admin,
            is_manager=is_manager,
            ttl_minutes=int(getattr(settings, "JWT_TTL_MINUTES", None) or 60),
        )

    await state.clear()
    interval = f"{st.strftime('%H:%M')}–{et.strftime('%H:%M')} ({int(hours2)} часов)"
    text = f"✅ {msg}\n\n{interval}"
    try:
        await edit_html(cb, format_plain_url(text, url), reply_markup=None)
    except Exception:
        await send_html(cb.message, format_plain_url(text, url))


@router.callback_query(F.data == "sched_em_replace_yes")
async def schedule_emergency_replace_yes(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.answer()
    except Exception:
        pass
    st = await state.get_data()
    day = str(st.get("day") or "").strip()
    st_s = str(st.get("start_time") or "").strip() or "10:00"
    et_s = str(st.get("end_time") or "").strip() or "18:00"
    comment = str(st.get("comment") or "").strip() or None
    target_user_id = int(st.get("target_user_id") or 0)

    parsed = _parse_time_range_hhmm(f"{st_s}-{et_s}")
    if parsed is None:
        await edit_html(cb, "Недостаточно данных для создания смены.")
        await state.clear()
        return
    st0, et0 = parsed
    st_n, et_n = normalize_shift_times(kind="work", start_time=st0, end_time=et0)
    h_int = calc_int_hours_from_times(start_time=st_n, end_time=et_n) if st_n is not None and et_n is not None else None

    if not day or h_int is None or target_user_id <= 0:
        await edit_html(cb, "Можно только целые часы. Выберите другое время.")
        await state.clear()
        return

    async with get_async_session() as session:
        msg, ok, st, et, hours2 = await _create_or_replace_emergency(
            session=session,
            target_user_id=target_user_id,
            day=day,
            start_time=st_n,
            end_time=et_n,
            comment=comment,
            replace=True,
        )
        actor_tg_id = int(st.get("actor_tg_id") or 0)
        is_admin = bool(st.get("is_admin"))
        is_manager = bool(st.get("is_manager"))
        tok_user = (
            await session.execute(select(User).where(User.id == int(target_user_id)).where(User.is_deleted == False))
        ).scalar_one_or_none()
        url = ""
        if tok_user is not None:
            url = await build_schedule_magic_link(
                session=session,
                user=tok_user,
                is_admin=is_admin,
                is_manager=is_manager,
                ttl_minutes=int(getattr(settings, "JWT_TTL_MINUTES", None) or 60),
            )

    await state.clear()
    if url:
        interval = f"{st.strftime('%H:%M')}–{et.strftime('%H:%M')} ({int(hours2)} часов)"
        await edit_html(cb, format_plain_url(f"✅ {msg}\n\n{interval}", url), reply_markup=None)
    else:
        interval = f"{st.strftime('%H:%M')}–{et.strftime('%H:%M')} ({int(hours2)} часов)"
        await edit_html(cb, f"✅ {msg}\n\n{interval}")
