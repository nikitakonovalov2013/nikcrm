from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


PAGE_SIZE = 8


def stocks_menu_kb(can_manage: bool, expanded: bool = False, can_toggle: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if can_manage:
        rows.append(
            [
                InlineKeyboardButton(text="➖ Расход", callback_data="stocks:op:out"),
                InlineKeyboardButton(text="➕ Пополнение", callback_data="stocks:op:in"),
            ]
        )
    if can_toggle:
        if expanded:
            rows.append([InlineKeyboardButton(text="Свернуть", callback_data="stocks:compact")])
        else:
            rows.append([InlineKeyboardButton(text="Показать всё", callback_data="stocks:all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stocks_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="stocks:cancel")]])


def stocks_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data="stocks:confirm"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="stocks:cancel"),
            ]
        ]
    )


def materials_page_kb(materials: list[tuple[int, str]], page: int) -> InlineKeyboardMarkup:
    total = len(materials)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    end = min(total, start + PAGE_SIZE)

    rows = [[InlineKeyboardButton(text=name, callback_data=f"stocks:mat:{mid}")] for mid, name in materials[start:end]]

    nav = []
    if pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"stocks:page:{page-1}"))
        nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="stocks:noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="➡️", callback_data=f"stocks:page:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="stocks:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
