from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def rr_menu_kb(can_manage: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="📅 Отчёт за сегодня", callback_data="rr:today")])
    rows.append([InlineKeyboardButton(text="🗓 Отчёт за период", callback_data="rr:period")])
    if can_manage:
        rows.append([InlineKeyboardButton(text="⏰ Настройки напоминаний", callback_data="rr:settings")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="rr:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rr_report_kb(can_send_to_chat: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_send_to_chat:
        rows.append([InlineKeyboardButton(text="📤 Отправить в чат", callback_data="rr:send")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="rr:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rr_period_presets_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="7 дней", callback_data="rr:preset:7"),
                InlineKeyboardButton(text="14 дней", callback_data="rr:preset:14"),
                InlineKeyboardButton(text="30 дней", callback_data="rr:preset:30"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="rr:menu")],
        ]
    )


def rr_settings_kb(enabled: bool, skip_weekends: bool, send_admins: bool, send_managers: bool, daily_report: bool) -> InlineKeyboardMarkup:
    def onoff(v: bool) -> str:
        return "✅" if v else "❌"

    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text=f"Напоминания: {onoff(enabled)}", callback_data="rr:set:enabled")])
    rows.append([InlineKeyboardButton(text="Время напоминания", callback_data="rr:set:reminder_time")])
    rows.append([InlineKeyboardButton(text=f"Не слать в выходные: {onoff(skip_weekends)}", callback_data="rr:set:skip_weekends")])
    rows.append([InlineKeyboardButton(text=f"Получатели: админы {onoff(send_admins)}", callback_data="rr:set:send_admins")])
    rows.append([InlineKeyboardButton(text=f"Получатели: руководители {onoff(send_managers)}", callback_data="rr:set:send_managers")])
    rows.append([InlineKeyboardButton(text=f"Ежедневный отчёт: {onoff(daily_report)}", callback_data="rr:set:daily_report")])
    rows.append([InlineKeyboardButton(text="Время авто-отчёта", callback_data="rr:set:daily_report_time")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="rr:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
