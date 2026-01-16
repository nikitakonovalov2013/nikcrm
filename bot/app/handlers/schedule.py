from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from shared.config import settings
from shared.db import get_async_session
from shared.enums import UserStatus
from shared.permissions import role_flags
from sqlalchemy import select

from bot.app.guards.user_guard import ensure_registered_or_reply
from bot.app.keyboards.main import main_menu_kb
from bot.app.utils.urls import build_schedule_magic_link
from bot.app.utils.telegram import edit_html, send_html, send_new_and_delete_active
from bot.app.utils.html import format_plain_url
from bot.app.states.schedule import ScheduleEmergencyState
from shared.models import WorkShiftDay, User, ShiftInstance, ShiftSwapRequest
from shared.enums import ShiftInstanceStatus
from shared.enums import ShiftSwapRequestStatus
from shared.utils import MOSCOW_TZ


router = Router()
_logger = logging.getLogger(__name__)


def _kb_cancel_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="sched_em_cancel")]])


def _kb_emergency_hours() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="8ч", callback_data="sched_em_h:8"),
            InlineKeyboardButton(text="10ч", callback_data="sched_em_h:10"),
            InlineKeyboardButton(text="12ч", callback_data="sched_em_h:12"),
        ],
        [InlineKeyboardButton(text="Отмена", callback_data="sched_em_cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


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

    if not (user.status == UserStatus.APPROVED or (int(message.from_user.id) in settings.admin_ids)):
        await message.answer(
            "⏳ Раздел «График работы» доступен только одобренным сотрудникам.",
            reply_markup=main_menu_kb(user.status, message.from_user.id, user.position),
        )
        return

    r = role_flags(
        tg_id=int(message.from_user.id),
        admin_ids=settings.admin_ids,
        status=user.status,
        position=user.position,
    )
    is_admin = bool(r.is_admin)
    is_manager = bool(r.is_manager)

    await state.clear()
    async with get_async_session() as session:
        text, kb = await _render_schedule_menu(session=session, user=user, is_admin=is_admin, is_manager=is_manager)
    await send_new_and_delete_active(message=message, state=state, text=text, reply_markup=kb)


 

@router.callback_query(F.data.in_({"sched_menu:open", "sched_menu:refresh"}))
async def schedule_menu_open(cb: CallbackQuery, state: FSMContext):
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    if user.status == UserStatus.BLACKLISTED:
        await edit_html(cb, "🚫 Доступ ограничен.")
        return

    if not (user.status == UserStatus.APPROVED or (int(cb.from_user.id) in settings.admin_ids)):
        await edit_html(cb, "⏳ Раздел «График работы» доступен только одобренным сотрудникам.")
        return

    r = role_flags(
        tg_id=int(cb.from_user.id),
        admin_ids=settings.admin_ids,
        status=user.status,
        position=user.position,
    )

    async with get_async_session() as session:
        text, kb = await _render_schedule_menu(session=session, user=user, is_admin=bool(r.is_admin), is_manager=bool(r.is_manager))
    await state.clear()
    await edit_html(cb, text, reply_markup=kb)


@router.callback_query(F.data == "sched_menu:back")
async def schedule_menu_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.edit_text("Ок.")
    except Exception:
        pass


@router.callback_query(F.data == "sched_em_from_menu")
async def schedule_emergency_start_from_menu(cb: CallbackQuery, state: FSMContext):
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    if user.status == UserStatus.BLACKLISTED:
        await edit_html(cb, "🚫 Доступ ограничен.")
        return

    if not (user.status == UserStatus.APPROVED or (int(cb.from_user.id) in settings.admin_ids)):
        await edit_html(cb, "Раздел доступен только одобренным сотрудникам.")
        return

    r = role_flags(
        tg_id=int(cb.from_user.id),
        admin_ids=settings.admin_ids,
        status=user.status,
        position=user.position,
    )
    await state.clear()
    await state.set_state(ScheduleEmergencyState.pick_hours)
    await state.update_data(
        actor_tg_id=int(cb.from_user.id),
        is_admin=bool(r.is_admin),
        is_manager=bool(r.is_manager),
        target_user_id=int(getattr(user, "id")),
        active_bot_chat_id=int(cb.message.chat.id) if cb.message else None,
        active_bot_message_id=int(cb.message.message_id) if cb.message else None,
    )

    await edit_html(cb, "⚡ Экстренная смена\n\nВыберите длительность:", reply_markup=_kb_emergency_hours())


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
    today = datetime.now(MOSCOW_TZ).date()
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
        )
    ).scalar_one_or_none()

    swap = (
        await session.execute(
            select(ShiftSwapRequest)
            .where(ShiftSwapRequest.from_user_id == int(user.id))
            .where(ShiftSwapRequest.day == today)
            .where(ShiftSwapRequest.status == ShiftSwapRequestStatus.OPEN)
        )
    ).scalar_one_or_none()

    has_plan_work = bool(plan is not None and str(getattr(plan, "kind", "")) == "work")
    planned_hours = int(getattr(plan, "hours", 0) or 0) if has_plan_work else 0
    plan_txt = (f"смена {planned_hours}ч" if has_plan_work else "нет смены")
    if bool(getattr(plan, "is_emergency", False)):
        plan_txt += " ⚡"
    if swap is not None:
        plan_txt += " 🆘"

    st = str(getattr(shift, "status", "") or "") if shift is not None else None
    status_txt = _ru_shift_status(st)
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

    # Buttons
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    rows: list[list[InlineKeyboardButton]] = []
    if is_admin or is_manager:
        rows.append([InlineKeyboardButton(text="✅ На подтверждении", callback_data="sched_pending:page:0")])
    # Start shift only if planned and not started
    if has_plan_work and (shift is None or str(getattr(shift, "status", "")) in {"", "planned"}):
        rows.append([InlineKeyboardButton(text="✅ Начать смену", callback_data=f"shift:start:{today.isoformat()}")])
    # Emergency start if there is no plan (spec: can start even without planned shift)
    if (not has_plan_work) and (shift is None or str(getattr(shift, "status", "")) in {"", "planned"}):
        rows.append([InlineKeyboardButton(text="⚡ Начать экстренную смену", callback_data=f"shift:start:{today.isoformat()}")])
    # Close shift only if started
    if shift is not None and str(getattr(shift, "status", "")) == "started":
        rows.append([InlineKeyboardButton(text="⏹ Закрыть смену", callback_data=f"shift:close:{int(getattr(shift,'id'))}")])
    # Emergency always available
    rows.append([InlineKeyboardButton(text="⚡ Экстренная смена", callback_data="sched_em_from_menu")])
    # Swap only if planned work and not started
    if has_plan_work and (shift is None or str(getattr(shift, "status", "")) in {"", "planned"}):
        rows.append([InlineKeyboardButton(text="🆘 Нужна замена", callback_data=f"swap:need:{today.isoformat()}")])
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="sched_menu:refresh")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="sched_menu:back")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return text, kb


@router.callback_query(F.data == "sched_em_cancel")
async def schedule_emergency_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await edit_html(cb, "Отменено.")
    except Exception:
        try:
            await cb.message.answer("Отменено.")
        except Exception:
            pass


@router.callback_query(F.data.startswith("sched_em_h:"))
async def schedule_emergency_pick_hours(cb: CallbackQuery, state: FSMContext):
    data = str(cb.data or "")
    try:
        hours = int(data.split(":", 1)[1])
    except Exception:
        await edit_html(cb, "Не удалось распознать часы.")
        return

    if hours not in {8, 10, 12}:
        await edit_html(cb, "Неверные часы.")
        return

    st = await state.get_data()
    is_admin_or_manager = bool(st.get("is_admin") or st.get("is_manager"))
    await state.update_data(hours=hours)

    if is_admin_or_manager:
        await state.set_state(ScheduleEmergencyState.pick_date_mode)
        async with get_async_session() as session:
            kb = await _kb_pick_user(session=session, page=0)
        await edit_html(cb, "Для кого открыть экстренную смену?", reply_markup=kb)
        return

    await state.set_state(ScheduleEmergencyState.pick_date_mode)
    await edit_html(cb, "На какую дату открыть смену?", reply_markup=_kb_emergency_date_mode())


@router.callback_query(F.data.startswith("sched_em_user_page:"))
async def schedule_emergency_user_page(cb: CallbackQuery, state: FSMContext):
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
    st = await state.get_data()
    day = str(st.get("day") or "").strip()
    hours = int(st.get("hours") or 0)
    await state.update_data(comment="")
    await state.set_state(ScheduleEmergencyState.confirm)
    await edit_html(cb, f"Подтвердите создание экстренной смены:\n\nДата: {day}\nДлительность: {hours}ч", reply_markup=_kb_emergency_confirm())


@router.message(ScheduleEmergencyState.input_comment)
async def schedule_emergency_input_comment(message: Message, state: FSMContext):
    txt = str(message.text or "").strip()
    await state.update_data(comment=txt)
    st = await state.get_data()
    day = str(st.get("day") or "").strip()
    hours = int(st.get("hours") or 0)
    await state.set_state(ScheduleEmergencyState.confirm)
    try:
        await message.delete()
    except Exception:
        pass
    await send_new_and_delete_active(
        message=message,
        state=state,
        text=f"Подтвердите создание экстренной смены:\n\nДата: {day}\nДлительность: {hours}ч" + (f"\nКомментарий: {txt}" if txt else ""),
        reply_markup=_kb_emergency_confirm(),
    )


async def _create_or_replace_emergency(*, session, target_user_id: int, day: str, hours: int, comment: str | None, replace: bool) -> tuple[str, bool]:
    from datetime import datetime as _dt

    d = _dt.strptime(str(day), "%Y-%m-%d").date()

    existing = (
        await session.execute(select(WorkShiftDay).where(WorkShiftDay.user_id == int(target_user_id)).where(WorkShiftDay.day == d))
    ).scalar_one_or_none()

    if existing is not None:
        if bool(getattr(existing, "is_emergency", False)):
            existing.kind = "work"
            existing.hours = int(hours)
            existing.comment = comment
            await session.flush()
            return ("Обновил существующую экстренную смену.", True)

        if not replace:
            return ("Смена уже запланирована. Заменить?", False)

        existing.kind = "work"
        existing.hours = int(hours)
        existing.is_emergency = True
        existing.comment = comment
        await session.flush()
        return ("Заменил плановую смену на экстренную.", True)

    row = WorkShiftDay(user_id=int(target_user_id), day=d, kind="work", hours=int(hours), is_emergency=True, comment=comment)
    session.add(row)
    await session.flush()
    return ("Создал экстренную смену.", True)


@router.callback_query(F.data == "sched_em_confirm")
async def schedule_emergency_confirm(cb: CallbackQuery, state: FSMContext):
    st = await state.get_data()
    day = str(st.get("day") or "").strip()
    hours = int(st.get("hours") or 0)
    comment = str(st.get("comment") or "").strip() or None
    target_user_id = int(st.get("target_user_id") or 0)

    if not day or hours not in {8, 10, 12} or target_user_id <= 0:
        await edit_html(cb, "Недостаточно данных для создания смены.")
        await state.clear()
        return

    async with get_async_session() as session:
        msg, ok = await _create_or_replace_emergency(
            session=session,
            target_user_id=target_user_id,
            day=day,
            hours=hours,
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
    try:
        await edit_html(cb, format_plain_url(f"✅ {msg}", url), reply_markup=None)
    except Exception:
        await send_html(cb.message, format_plain_url(f"✅ {msg}", url))


@router.callback_query(F.data == "sched_em_replace_yes")
async def schedule_emergency_replace_yes(cb: CallbackQuery, state: FSMContext):
    st = await state.get_data()
    day = str(st.get("day") or "").strip()
    hours = int(st.get("hours") or 0)
    comment = str(st.get("comment") or "").strip() or None
    target_user_id = int(st.get("target_user_id") or 0)

    async with get_async_session() as session:
        msg, ok = await _create_or_replace_emergency(
            session=session,
            target_user_id=target_user_id,
            day=day,
            hours=hours,
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
        await edit_html(cb, format_plain_url(f"✅ {msg}", url), reply_markup=None)
    else:
        await edit_html(cb, f"✅ {msg}")
