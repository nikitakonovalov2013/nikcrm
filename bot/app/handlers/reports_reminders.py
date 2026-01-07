from __future__ import annotations

import logging
from datetime import datetime, timedelta, date, time
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from shared.config import settings
from shared.db import get_async_session
from shared.enums import UserStatus, Position
from bot.app.keyboards.main import main_menu_kb
from bot.app.keyboards.reports_reminders import rr_menu_kb, rr_report_kb, rr_period_presets_kb, rr_settings_kb
from bot.app.repository.users import UserRepository
from bot.app.repository.reminders_settings import ReminderSettingsRepository
from bot.app.services.stocks_reports import build_report
from bot.app.services.stocks_reports_format import format_report_html
from bot.app.services.reminders_scheduler import reschedule_from_db
from bot.app.states.reports_reminders import ReportsRemindersState
from bot.app.utils.telegram import edit_html, edit_html_by_id_from_message
from bot.app.utils.datetime_fmt import format_date_ru
from shared.permissions import can_access_reports
from bot.app.guards.user_guard import ensure_registered_or_reply


router = Router()
_logger = logging.getLogger(__name__)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(getattr(settings, "TIMEZONE", "Europe/Moscow"))
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids


async def _get_user(tg_id: int):
    async with get_async_session() as session:
        urepo = UserRepository(session)
        return await urepo.get_by_tg_id(tg_id)


def _can_manage(user) -> bool:
    return can_access_reports(
        tg_id=int(user.tg_id),
        admin_ids=settings.admin_ids,
        status=user.status,
        position=user.position,
    )


def _can_open_menu(user) -> bool:
    return _can_manage(user)


def _parse_date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%d.%m.%Y").date()
    except Exception:
        return None


def _fmt_time_hhmm(t: time | None) -> str:
    if not t:
        return "не установлено"
    return f"{t.hour:02d}:{t.minute:02d}"


def _settings_text(s) -> str:
    reminder_time_str = _fmt_time_hhmm(getattr(s, "reminder_time", None))
    daily_time_str = _fmt_time_hhmm(getattr(s, "daily_report_time", None))
    return (
        "⏰ <b>Настройки напоминаний</b>\n\n"
        f"<b>Время напоминания:</b> {reminder_time_str}\n"
        f"<b>Время авто-отчёта:</b> {daily_time_str}\n\n"
        "<b>Напоминание</b> — уведомляет в заданное время, чтобы вы не забыли внести/проверить данные по остаткам.\n\n"
        "<b>Авто-отчёт</b> — автоматически отправляет сводку по складу в заданное время."
    )


async def _deny(cb: CallbackQuery, state: FSMContext, *, note: str = "⛔ Нет доступа") -> None:
    await state.clear()
    try:
        await cb.message.edit_text(f"{note}.")
    except Exception:
        pass


@router.message(F.text.in_({"Отчёты и напоминания", "📊 Отчёты и напоминания"}))
@router.message(Command("reports"))
async def rr_entry(message: Message, state: FSMContext):
    user = await ensure_registered_or_reply(message)
    if not user:
        return
    if not _can_open_menu(user):
        await message.answer("⛔ Нет доступа.", reply_markup=main_menu_kb(user.status, message.from_user.id, user.position))
        return

    await state.clear()
    can_manage = _can_manage(user)
    sent = await message.answer("📊 <b>Отчёты и напоминания</b>\n\nВыберите действие:", reply_markup=rr_menu_kb(can_manage))
    await state.update_data(menu_chat_id=sent.chat.id, menu_message_id=sent.message_id)


@router.callback_query(F.data == "rr:menu")
async def rr_menu(cb: CallbackQuery, state: FSMContext):
    user = await ensure_registered_or_reply(cb)
    if not user:
        return
    if not _can_open_menu(user):
        await _deny(cb, state)
        await cb.answer()
        return
    await state.clear()
    can_manage = _can_manage(user)
    await edit_html(cb, "📊 <b>Отчёты и напоминания</b>\n\nВыберите действие:", reply_markup=rr_menu_kb(can_manage))
    await cb.answer()


@router.callback_query(F.data == "rr:back")
async def rr_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    status = None
    position = None
    user = await _get_user(cb.from_user.id)
    if user:
        status = user.status
        position = user.position
    await cb.message.answer("Выберите действие ниже.", reply_markup=main_menu_kb(status, cb.from_user.id, position))
    await cb.answer()


@router.callback_query(F.data == "rr:today")
async def rr_today(cb: CallbackQuery, state: FSMContext):
    user = await _get_user(cb.from_user.id)
    if not user:
        await cb.answer()
        return
    if not _can_manage(user):
        await _deny(cb, state)
        await cb.answer()
        return

    tz = _tz()
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    async with get_async_session() as session:
        data = await build_report(session, start=start, end=end, events_limit=10)

    text = format_report_html("Отчёт за сегодня", data)
    await state.update_data(last_report_text=text)

    can_send = bool(getattr(settings, "REPORTS_CHAT_ID", 0))
    await edit_html(cb, text, reply_markup=rr_report_kb(can_send_to_chat=can_send))
    await cb.answer()


@router.callback_query(F.data == "rr:period")
async def rr_period(cb: CallbackQuery, state: FSMContext):
    user = await _get_user(cb.from_user.id)
    if not user:
        await cb.answer()
        return
    if not _can_manage(user):
        await _deny(cb, state)
        await cb.answer()
        return

    await state.clear()
    await state.set_state(ReportsRemindersState.period_from)
    await state.update_data(menu_chat_id=cb.message.chat.id, menu_message_id=cb.message.message_id)
    await edit_html(
        cb,
        "🗓 <b>Отчёт за период</b>\n\nВведите дату <b>С</b> в формате <b>ДД.ММ.ГГГГ</b> или выберите пресет:",
        reply_markup=rr_period_presets_kb(),
    )
    await cb.answer()


@router.callback_query(ReportsRemindersState.period_from, F.data.startswith("rr:preset:"))
async def rr_period_preset(cb: CallbackQuery, state: FSMContext):
    try:
        days = int(cb.data.split(":", 2)[2])
    except Exception:
        await cb.answer()
        return
    tz = _tz()
    today = datetime.now(tz).date()
    d_from = today - timedelta(days=days - 1)
    d_to = today
    await state.update_data(period_from=d_from.isoformat(), period_to=d_to.isoformat())

    start = datetime.combine(d_from, time(0, 0), tzinfo=tz)
    end = datetime.combine(d_to, time(23, 59, 59), tzinfo=tz)
    async with get_async_session() as session:
        data = await build_report(session, start=start, end=end, events_limit=10)

    text = format_report_html(f"Отчёт за {days} дней", data)
    await state.clear()
    await state.update_data(last_report_text=text)

    can_send = bool(getattr(settings, "REPORTS_CHAT_ID", 0))
    await edit_html(cb, text, reply_markup=rr_report_kb(can_send_to_chat=can_send))
    await cb.answer()


@router.message(ReportsRemindersState.period_from)
async def rr_period_from_input(message: Message, state: FSMContext):
    d = _parse_date(message.text or "")
    data = await state.get_data()
    menu_chat_id = data.get("menu_chat_id")
    menu_message_id = data.get("menu_message_id")
    if not menu_chat_id or not menu_message_id:
        # fallback: do not block flow
        menu_chat_id = message.chat.id
        menu_message_id = message.message_id
    if not d:
        await edit_html_by_id_from_message(
            message,
            chat_id=int(menu_chat_id),
            message_id=int(menu_message_id),
            text=(
                "🗓 <b>Отчёт за период</b>\n\n"
                "❌ Неверный формат. Введите дату <b>С</b> как <b>ДД.ММ.ГГГГ</b>."
            ),
            reply_markup=rr_period_presets_kb(),
        )
        return
    await state.update_data(period_from=d.isoformat())
    await state.set_state(ReportsRemindersState.period_to)
    await edit_html_by_id_from_message(
        message,
        chat_id=int(menu_chat_id),
        message_id=int(menu_message_id),
        text=(
            "🗓 <b>Отчёт за период</b>\n\n"
            f"Дата <b>С</b>: <b>{format_date_ru(d)}</b>\n\n"
            "Введите дату <b>ПО</b> как <b>ДД.ММ.ГГГГ</b> (включительно):"
        ),
    )


@router.message(ReportsRemindersState.period_to)
async def rr_period_to_input(message: Message, state: FSMContext):
    d_to = _parse_date(message.text or "")
    data = await state.get_data()
    menu_chat_id = data.get("menu_chat_id")
    menu_message_id = data.get("menu_message_id")
    if not menu_chat_id or not menu_message_id:
        menu_chat_id = message.chat.id
        menu_message_id = message.message_id
    if not d_to:
        await edit_html_by_id_from_message(
            message,
            chat_id=int(menu_chat_id),
            message_id=int(menu_message_id),
            text="🗓 <b>Отчёт за период</b>\n\n❌ Неверный формат. Введите дату <b>ПО</b> как <b>ДД.ММ.ГГГГ</b>.",
        )
        return

    d_from_raw = data.get("period_from")
    try:
        d_from = date.fromisoformat(str(d_from_raw))
    except Exception:
        await state.clear()
        await edit_html_by_id_from_message(
            message,
            chat_id=int(menu_chat_id),
            message_id=int(menu_message_id),
            text="❌ Не удалось прочитать дату С. Нажмите «Отчёт за период» и начните заново.",
        )
        return

    if d_to < d_from:
        await edit_html_by_id_from_message(
            message,
            chat_id=int(menu_chat_id),
            message_id=int(menu_message_id),
            text=(
                "🗓 <b>Отчёт за период</b>\n\n"
                f"Дата <b>С</b>: <b>{format_date_ru(d_from)}</b>\n\n"
                "❌ Дата <b>ПО</b> не может быть меньше даты <b>С</b>. Введите дату <b>ПО</b> ещё раз:"
            ),
        )
        return

    tz = _tz()
    start = datetime.combine(d_from, time(0, 0), tzinfo=tz)
    end = datetime.combine(d_to, time(23, 59, 59), tzinfo=tz)

    async with get_async_session() as session:
        rep = await build_report(session, start=start, end=end, events_limit=10)

    title = f"Отчёт за период {format_date_ru(d_from)} — {format_date_ru(d_to)}"
    text = format_report_html(title, rep)
    await state.clear()
    await state.update_data(last_report_text=text)

    can_send = bool(getattr(settings, "REPORTS_CHAT_ID", 0))
    await edit_html_by_id_from_message(
        message,
        chat_id=int(menu_chat_id),
        message_id=int(menu_message_id),
        text=text,
        reply_markup=rr_report_kb(can_send_to_chat=can_send),
    )


@router.callback_query(F.data == "rr:send")
async def rr_send(cb: CallbackQuery, state: FSMContext):
    user = await _get_user(cb.from_user.id)
    if not user or not _can_manage(user):
        await _deny(cb, state)
        await cb.answer()
        return

    chat_id = int(getattr(settings, "REPORTS_CHAT_ID", 0) or 0)
    if not chat_id:
        await cb.answer("REPORTS_CHAT_ID не настроен", show_alert=True)
        return

    data = await state.get_data()
    text = data.get("last_report_text")
    if not text:
        await cb.answer("Нет отчёта для отправки", show_alert=True)
        return

    try:
        await cb.bot.send_message(chat_id=chat_id, text=str(text))
        _logger.info("report sent", extra={"chat_id": chat_id, "from": cb.from_user.id})
        await cb.answer("Отправлено")
    except Exception:
        _logger.exception("failed to send report", extra={"chat_id": chat_id})
        await cb.answer("Ошибка отправки", show_alert=True)


@router.callback_query(F.data == "rr:settings")
async def rr_settings(cb: CallbackQuery, state: FSMContext):
    user = await _get_user(cb.from_user.id)
    if not user or not _can_manage(user):
        await _deny(cb, state)
        await cb.answer()
        return

    async with get_async_session() as session:
        repo = ReminderSettingsRepository(session)
        s = await repo.get_singleton()

    text = _settings_text(s)
    await edit_html(
        cb,
        text,
        reply_markup=rr_settings_kb(
            enabled=s.reminders_enabled,
            skip_weekends=s.skip_weekends,
            send_admins=s.send_to_admins,
            send_managers=s.send_to_managers,
            daily_report=s.daily_report_enabled,
        ),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("rr:set:"))
async def rr_settings_toggle(cb: CallbackQuery, state: FSMContext):
    user = await _get_user(cb.from_user.id)
    if not user or not _can_manage(user):
        await _deny(cb, state)
        await cb.answer()
        return

    key = cb.data.split(":", 2)[2]
    async with get_async_session() as session:
        repo = ReminderSettingsRepository(session)
        s = await repo.get_singleton()
        if key == "enabled":
            s.reminders_enabled = not s.reminders_enabled
        elif key == "skip_weekends":
            s.skip_weekends = not s.skip_weekends
        elif key == "send_admins":
            s.send_to_admins = not s.send_to_admins
        elif key == "send_managers":
            s.send_to_managers = not s.send_to_managers
        elif key == "daily_report":
            s.daily_report_enabled = not s.daily_report_enabled
        elif key == "reminder_time":
            await state.update_data(menu_chat_id=cb.message.chat.id, menu_message_id=cb.message.message_id)
            await state.set_state(ReportsRemindersState.reminder_time)
            await edit_html(cb, "Введите время напоминания в формате <b>ЧЧ:ММ</b> (например 16:00):")
            await cb.answer()
            return
        elif key == "daily_report_time":
            await state.update_data(menu_chat_id=cb.message.chat.id, menu_message_id=cb.message.message_id)
            await state.set_state(ReportsRemindersState.daily_report_time)
            await edit_html(cb, "Введите время авто-отчёта в формате <b>ЧЧ:ММ</b> (например 18:00):")
            await cb.answer()
            return

        await session.flush()
        await session.refresh(s)

    await reschedule_from_db()

    text = _settings_text(s)
    await edit_html(
        cb,
        text,
        reply_markup=rr_settings_kb(
            enabled=s.reminders_enabled,
            skip_weekends=s.skip_weekends,
            send_admins=s.send_to_admins,
            send_managers=s.send_to_managers,
            daily_report=s.daily_report_enabled,
        ),
    )
    await cb.answer()


def _parse_time(text: str) -> time | None:
    try:
        return datetime.strptime(text.strip(), "%H:%M").time()
    except Exception:
        return None


@router.message(ReportsRemindersState.reminder_time)
async def rr_set_reminder_time(message: Message, state: FSMContext):
    t = _parse_time(message.text or "")
    data = await state.get_data()
    menu_chat_id = data.get("menu_chat_id")
    menu_message_id = data.get("menu_message_id")
    if not t:
        if menu_chat_id and menu_message_id:
            async with get_async_session() as session:
                repo = ReminderSettingsRepository(session)
                s = await repo.get_singleton()
            await edit_html_by_id_from_message(
                message,
                chat_id=int(menu_chat_id),
                message_id=int(menu_message_id),
                text="❌ Неверный формат. Введите время как <b>ЧЧ:ММ</b>.\n\n" + _settings_text(s),
                reply_markup=rr_settings_kb(
                    enabled=s.reminders_enabled,
                    skip_weekends=s.skip_weekends,
                    send_admins=s.send_to_admins,
                    send_managers=s.send_to_managers,
                    daily_report=s.daily_report_enabled,
                ),
            )
        return

    async with get_async_session() as session:
        repo = ReminderSettingsRepository(session)
        s = await repo.set_reminder_time(t)

    await reschedule_from_db()
    await state.clear()
    if menu_chat_id and menu_message_id:
        async with get_async_session() as session:
            repo = ReminderSettingsRepository(session)
            s2 = await repo.get_singleton()
        await edit_html_by_id_from_message(
            message,
            chat_id=int(menu_chat_id),
            message_id=int(menu_message_id),
            text=_settings_text(s2),
            reply_markup=rr_settings_kb(
                enabled=s2.reminders_enabled,
                skip_weekends=s2.skip_weekends,
                send_admins=s2.send_to_admins,
                send_managers=s2.send_to_managers,
                daily_report=s2.daily_report_enabled,
            ),
        )


@router.message(ReportsRemindersState.daily_report_time)
async def rr_set_daily_report_time(message: Message, state: FSMContext):
    t = _parse_time(message.text or "")
    data = await state.get_data()
    menu_chat_id = data.get("menu_chat_id")
    menu_message_id = data.get("menu_message_id")
    if not t:
        if menu_chat_id and menu_message_id:
            async with get_async_session() as session:
                repo = ReminderSettingsRepository(session)
                s = await repo.get_singleton()
            await edit_html_by_id_from_message(
                message,
                chat_id=int(menu_chat_id),
                message_id=int(menu_message_id),
                text="❌ Неверный формат. Введите время как <b>ЧЧ:ММ</b>.\n\n" + _settings_text(s),
                reply_markup=rr_settings_kb(
                    enabled=s.reminders_enabled,
                    skip_weekends=s.skip_weekends,
                    send_admins=s.send_to_admins,
                    send_managers=s.send_to_managers,
                    daily_report=s.daily_report_enabled,
                ),
            )
        return

    async with get_async_session() as session:
        repo = ReminderSettingsRepository(session)
        s = await repo.set_daily_report_time(t)

    await reschedule_from_db()
    await state.clear()
    if menu_chat_id and menu_message_id:
        async with get_async_session() as session:
            repo = ReminderSettingsRepository(session)
            s2 = await repo.get_singleton()
        await edit_html_by_id_from_message(
            message,
            chat_id=int(menu_chat_id),
            message_id=int(menu_message_id),
            text=_settings_text(s2),
            reply_markup=rr_settings_kb(
                enabled=s2.reminders_enabled,
                skip_weekends=s2.skip_weekends,
                send_admins=s2.send_to_admins,
                send_managers=s2.send_to_managers,
                daily_report=s2.daily_report_enabled,
            ),
        )
