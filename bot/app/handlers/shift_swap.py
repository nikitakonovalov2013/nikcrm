from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton

from sqlalchemy import select

from shared.config import settings
from shared.db import get_async_session
from shared.enums import UserStatus, Position, ShiftInstanceStatus, ShiftSwapRequestStatus
from shared.permissions import role_flags
from shared.utils import MOSCOW_TZ, utc_now
from shared.models import User, WorkShiftDay, ShiftInstance, ShiftSwapRequest

from bot.app.guards.user_guard import ensure_registered_or_reply
from bot.app.states.shift_swap import ShiftSwapCreateState
from bot.app.utils.telegram import edit_html, send_new_and_delete_active
from bot.app.utils.urls import build_schedule_magic_link
from bot.app.utils.html import esc


router = Router()
_logger = logging.getLogger(__name__)


REASONS = {
    "sick": "🤒 Заболел",
    "force": "🏥 Форс-мажор",
    "other": "🕒 Другое",
}


def _kb_reasons(day: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=REASONS["sick"], callback_data=f"swap:reason:{day}:sick")],
            [InlineKeyboardButton(text=REASONS["force"], callback_data=f"swap:reason:{day}:force")],
            [InlineKeyboardButton(text=REASONS["other"], callback_data=f"swap:reason:{day}:other")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="swap:cancel")],
        ]
    )


def _kb_cancel_only() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="swap:cancel")]])


def _kb_bonus(day: str) -> InlineKeyboardMarkup:
    # Minimal defaults
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Без доплаты", callback_data=f"swap:bonus:{day}:0")],
            [InlineKeyboardButton(text="+500", callback_data=f"swap:bonus:{day}:500")],
            [InlineKeyboardButton(text="+1000", callback_data=f"swap:bonus:{day}:1000")],
            [InlineKeyboardButton(text="Ввести сумму", callback_data=f"swap:bonus_custom:{day}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="swap:cancel")],
        ]
    )


def _kb_confirm(day: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить запрос", callback_data=f"swap:send:{day}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="swap:cancel"),
            ]
        ]
    )


def _kb_call_to_colleagues(*, req_id: int, day: str, web_link: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я выйду на замену", callback_data=f"swap:accept:{req_id}")],
            [InlineKeyboardButton(text="❌ Не могу", callback_data=f"swap:decline:{req_id}")],
            [InlineKeyboardButton(text="🔗 Открыть график", url=web_link)],
        ]
    )


def _is_manager_or_admin(user: User, tg_id: int) -> bool:
    r = role_flags(tg_id=int(tg_id), admin_ids=settings.admin_ids, status=user.status, position=user.position)
    return bool(r.is_admin or r.is_manager)


async def _check_can_request(session, *, user: User, day) -> tuple[bool, str, WorkShiftDay | None]:
    plan = (
        await session.execute(
            select(WorkShiftDay)
            .where(WorkShiftDay.user_id == int(user.id))
            .where(WorkShiftDay.day == day)
        )
    ).scalar_one_or_none()

    if plan is None or str(getattr(plan, "kind", "")) != "work":
        return False, "Запрос замены доступен только для плановой смены.", None

    if bool(getattr(plan, "is_emergency", False)):
        return False, "Это экстренная смена — запрос замены не нужен.", plan

    started = (
        await session.execute(
            select(ShiftInstance)
            .where(ShiftInstance.user_id == int(user.id))
            .where(ShiftInstance.day == day)
            .where(ShiftInstance.status == ShiftInstanceStatus.STARTED)
        )
    ).scalar_one_or_none()
    if started is not None:
        return False, "Смена уже открыта. Обратитесь к руководителю.", plan

    existing_open = (
        await session.execute(
            select(ShiftSwapRequest)
            .where(ShiftSwapRequest.from_user_id == int(user.id))
            .where(ShiftSwapRequest.day == day)
            .where(ShiftSwapRequest.status == ShiftSwapRequestStatus.OPEN)
        )
    ).scalar_one_or_none()
    if existing_open is not None:
        return False, "Запрос замены уже отправлен. Ждём отклика.", plan

    return True, "", plan


@router.callback_query(F.data == "swap:cancel")
async def swap_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await edit_html(cb, "Отменено.")


@router.callback_query(F.data.startswith("swap:need:"))
async def swap_need(cb: CallbackQuery, state: FSMContext):
    user = await ensure_registered_or_reply(cb)
    if not user:
        return
    if user.status != UserStatus.APPROVED and int(cb.from_user.id) not in settings.admin_ids:
        await edit_html(cb, "⛔ Нет доступа.")
        return

    day_s = str(cb.data).split(":", 2)[2]
    try:
        d = datetime.strptime(day_s, "%Y-%m-%d").date()
    except Exception:
        await edit_html(cb, "Неверная дата.")
        return

    async with get_async_session() as session:
        ok, msg, _plan = await _check_can_request(session, user=user, day=d)
        if not ok:
            await state.clear()
            await edit_html(cb, msg)
            return

    await state.clear()
    await state.set_state(ShiftSwapCreateState.reason)
    await state.update_data(
        day=day_s,
        active_bot_chat_id=int(cb.message.chat.id) if cb.message else None,
        active_bot_message_id=int(cb.message.message_id) if cb.message else None,
    )
    await edit_html(cb, "Выберите причину:", reply_markup=_kb_reasons(day_s))


@router.callback_query(F.data.startswith("swap:reason:"))
async def swap_reason(cb: CallbackQuery, state: FSMContext):
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    parts = str(cb.data).split(":", 3)
    if len(parts) != 4:
        await edit_html(cb, "Неверные данные.")
        return

    day_s = parts[2]
    reason_key = parts[3]
    if reason_key not in REASONS:
        await edit_html(cb, "Неверная причина.")
        return

    await state.set_state(ShiftSwapCreateState.bonus_choice)
    await state.update_data(day=day_s, reason=reason_key)
    await edit_html(cb, "Предложить доплату за замену?", reply_markup=_kb_bonus(day_s))


@router.callback_query(F.data.startswith("swap:bonus_custom:"))
async def swap_bonus_custom(cb: CallbackQuery, state: FSMContext):
    parts = str(cb.data).split(":", 2)
    day_s = parts[2] if len(parts) == 3 else ""
    await state.set_state(ShiftSwapCreateState.bonus_custom)
    await state.update_data(day=day_s)
    await edit_html(cb, "Введите сумму доплаты (₽) числом:", reply_markup=_kb_cancel_only())


@router.callback_query(F.data.startswith("swap:bonus:"))
async def swap_bonus_pick(cb: CallbackQuery, state: FSMContext):
    parts = str(cb.data).split(":", 3)
    if len(parts) != 4:
        await edit_html(cb, "Неверные данные.")
        return

    day_s = parts[2]
    try:
        bonus = int(parts[3])
        if bonus < 0:
            raise ValueError
    except Exception:
        await edit_html(cb, "Неверная сумма.")
        return

    data = await state.get_data()
    reason_key = str(data.get("reason") or "")
    if reason_key not in REASONS:
        await edit_html(cb, "Сначала выберите причину.")
        return

    await state.set_state(ShiftSwapCreateState.confirm)
    await state.update_data(day=day_s, bonus=bonus)

    bonus_txt = "Без доплаты" if bonus == 0 else f"+{bonus} ₽"
    await edit_html(cb, f"Причина: {REASONS[reason_key]}\nДоплата: <b>{esc(bonus_txt)}</b>\n\nОтправить запрос замены?", reply_markup=_kb_confirm(day_s))


@router.message(ShiftSwapCreateState.bonus_custom)
async def swap_bonus_custom_enter(message: Message, state: FSMContext):
    user = await ensure_registered_or_reply(message)
    if not user:
        return

    try:
        bonus = int(str(message.text or "").strip())
        if bonus < 0:
            raise ValueError
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(
            message=message,
            state=state,
            text="Введите число (>=0).",
            reply_markup=_kb_cancel_only(),
        )
        return

    data = await state.get_data()
    day_s = str(data.get("day") or "")
    reason_key = str(data.get("reason") or "")
    if not day_s or reason_key not in REASONS:
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        await send_new_and_delete_active(message=message, state=state, text="Контекст потерян. Начните заново.")
        return

    await state.set_state(ShiftSwapCreateState.confirm)
    await state.update_data(bonus=bonus)

    bonus_txt = "Без доплаты" if bonus == 0 else f"+{bonus} ₽"
    try:
        await message.delete()
    except Exception:
        pass
    await send_new_and_delete_active(
        message=message,
        state=state,
        text=f"Причина: {REASONS[reason_key]}\nДоплата: <b>{esc(bonus_txt)}</b>\n\nОтправить запрос замены?",
        reply_markup=_kb_confirm(day_s),
    )


@router.callback_query(F.data.startswith("swap:send:"))
async def swap_send(cb: CallbackQuery, state: FSMContext):
    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    data = await state.get_data()
    day_s = str(data.get("day") or "")
    reason_key = str(data.get("reason") or "")
    bonus = int(data.get("bonus") or 0)

    try:
        d = datetime.strptime(day_s, "%Y-%m-%d").date()
    except Exception:
        await edit_html(cb, "Неверная дата.")
        return

    async with get_async_session() as session:
        ok, msg, plan = await _check_can_request(session, user=user, day=d)
        if not ok:
            await state.clear()
            await edit_html(cb, msg)
            return

        planned_hours = int(getattr(plan, "hours", 0) or 0) or None

        req = ShiftSwapRequest(
            day=d,
            from_user_id=int(user.id),
            planned_hours=planned_hours,
            reason=reason_key,
            bonus_amount=(bonus if bonus > 0 else None),
            status=ShiftSwapRequestStatus.OPEN,
        )
        session.add(req)
        await session.flush()

        # build web link for recipients
        url = await build_schedule_magic_link(
            session=session,
            user=user,
            is_admin=False,
            is_manager=False,
            ttl_minutes=60,
        )
        web_link = url

        # recipients: all approved staff except managers/admins and except requester
        res = await session.execute(
            select(User).where(User.status == UserStatus.APPROVED).where(User.is_deleted == False)
        )
        users = [u for (u,) in res.all()]

    bonus_txt = "без доплаты" if not req.bonus_amount else f"+{int(req.bonus_amount)} ₽"
    hours_txt = f"{int(req.planned_hours)}ч" if req.planned_hours else ""

    who = (" ".join([str(user.first_name or "").strip(), str(user.last_name or "").strip()]).strip()) or f"User #{int(user.id)}"
    txt = (
        f"🆘 <b>Нужна замена на смену</b>\n\n"
        f"Кто: <b>{esc(who)}</b>\n"
        f"Дата: <b>{day_s}</b>\n"
        + (f"Длительность: <b>{esc(hours_txt)}</b>\n" if hours_txt else "")
        + f"Причина: {REASONS.get(reason_key, reason_key)}\n"
        + f"Доплата: <b>{esc(bonus_txt)}</b>"
    )

    kb = _kb_call_to_colleagues(req_id=int(req.id), day=day_s, web_link=web_link)

    sent_cnt = 0
    for u in users:
        if int(getattr(u, "id")) == int(user.id):
            continue
        if int(getattr(u, "tg_id", 0) or 0) in set(int(x) for x in settings.admin_ids):
            continue
        if getattr(u, "position", None) == Position.MANAGER:
            continue
        chat_id = int(getattr(u, "tg_id", 0) or 0)
        if not chat_id:
            continue
        try:
            await cb.bot.send_message(chat_id=chat_id, text=txt, reply_markup=kb)
            sent_cnt += 1
        except Exception:
            _logger.exception("failed to send swap broadcast", extra={"chat_id": chat_id})

    await state.clear()
    await edit_html(cb, f"Готово. Запрос замены отправлен — ждём отклика.\n\nРазослано: {sent_cnt}")


@router.callback_query(F.data.startswith("swap:decline:"))
async def swap_decline(cb: CallbackQuery, state: FSMContext):
    try:
        await cb.message.edit_text("Ок, спасибо за ответ.")
    except Exception:
        pass


@router.callback_query(F.data.startswith("swap:accept:"))
async def swap_accept(cb: CallbackQuery, state: FSMContext):
    actor = await ensure_registered_or_reply(cb)
    if not actor:
        return

    try:
        req_id = int(str(cb.data).split(":", 2)[2])
    except Exception:
        await edit_html(cb, "Неверный запрос.")
        return

    async with get_async_session() as session:
        req = (
            await session.execute(
                select(ShiftSwapRequest)
                .where(ShiftSwapRequest.id == int(req_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if req is None:
            await edit_html(cb, "Запрос не найден.")
            return

        if req.status != ShiftSwapRequestStatus.OPEN:
            await edit_html(cb, "Замена уже найдена или запрос закрыт.")
            try:
                await cb.message.edit_text("Замена уже найдена или запрос закрыт.")
            except Exception:
                pass
            return

        # prevent manager/admin taking shifts as per requirement
        if actor.position == Position.MANAGER or int(actor.tg_id) in set(int(x) for x in settings.admin_ids):
            await edit_html(cb, "Руководители/админы не участвуют в замене.")
            return

        # lock-in accept
        req.status = ShiftSwapRequestStatus.ACCEPTED
        req.accepted_by_user_id = int(actor.id)
        req.closed_at = utc_now()

        # move plan: from_user -> off, actor -> work(hours)
        from_plan = (
            await session.execute(
                select(WorkShiftDay)
                .where(WorkShiftDay.user_id == int(req.from_user_id))
                .where(WorkShiftDay.day == req.day)
            )
        ).scalar_one_or_none()
        if from_plan is None or str(getattr(from_plan, "kind", "")) != "work" or bool(getattr(from_plan, "is_emergency", False)):
            await session.rollback()
            await edit_html(cb, "Запрос уже не актуален.")
            return

        # ensure from user hasn't started
        started = (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.user_id == int(req.from_user_id))
                .where(ShiftInstance.day == req.day)
                .where(ShiftInstance.status == ShiftInstanceStatus.STARTED)
            )
        ).scalar_one_or_none()
        if started is not None:
            await session.rollback()
            await edit_html(cb, "Смена уже открыта у сотрудника. Замена невозможна.")
            return

        # set from off
        from_plan.kind = "off"
        from_plan.hours = None

        # set actor work
        to_plan = (
            await session.execute(
                select(WorkShiftDay)
                .where(WorkShiftDay.user_id == int(actor.id))
                .where(WorkShiftDay.day == req.day)
            )
        ).scalar_one_or_none()
        if to_plan is None:
            to_plan = WorkShiftDay(user_id=int(actor.id), day=req.day, kind="work", hours=req.planned_hours, is_emergency=False)
            session.add(to_plan)
        else:
            to_plan.kind = "work"
            to_plan.hours = req.planned_hours
            to_plan.is_emergency = False

        await session.flush()

        from_user = (
            await session.execute(select(User).where(User.id == int(req.from_user_id)))
        ).scalar_one()

        # notify managers/admins
        res_mgr = await session.execute(
            select(User.tg_id)
            .where(User.status == UserStatus.APPROVED)
            .where(User.position == Position.MANAGER)
        )
        mgr_ids = set(int(x) for x in settings.admin_ids)
        for (tg_id,) in res_mgr.all():
            if tg_id:
                mgr_ids.add(int(tg_id))

    # UI feedback for clicker (delete/replace)
    try:
        await cb.message.edit_text("✅ Принято. Вы назначены на смену.")
    except Exception:
        pass

    # notify participants
    from_name = (" ".join([str(from_user.first_name or "").strip(), str(from_user.last_name or "").strip()]).strip()) or f"User #{int(from_user.id)}"
    to_name = (" ".join([str(actor.first_name or "").strip(), str(actor.last_name or "").strip()]).strip()) or f"User #{int(actor.id)}"

    try:
        if getattr(actor, "tg_id", None):
            await cb.bot.send_message(chat_id=int(actor.tg_id), text=f"✅ Вы назначены на смену {req.day}. Начать смену можно в 08:00 или вручную.")
    except Exception:
        _logger.exception("notify actor failed")

    try:
        if getattr(from_user, "tg_id", None):
            await cb.bot.send_message(chat_id=int(from_user.tg_id), text=f"✅ Замена найдена: <b>{esc(to_name)}</b>. Вы сняты со смены {req.day}.")
    except Exception:
        _logger.exception("notify from_user failed")

    bonus_txt = "—" if not req.bonus_amount else f"{int(req.bonus_amount)} ₽"
    reason_txt = REASONS.get(str(req.reason), str(req.reason))
    mgr_text = (
        f"🔁 <b>Замена смены</b>\n\n"
        f"Дата: <b>{req.day}</b>\n"
        f"Было: <b>{esc(from_name)}</b>\n"
        f"Стало: <b>{esc(to_name)}</b>\n"
        f"Доплата: <b>{esc(bonus_txt)}</b>\n"
        f"Причина: {reason_txt}"
    )

    for chat_id in sorted(mgr_ids):
        try:
            await cb.bot.send_message(chat_id=chat_id, text=mgr_text)
        except Exception:
            _logger.exception("notify manager failed", extra={"chat_id": chat_id})


 
