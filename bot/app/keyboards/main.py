from typing import Optional
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from shared.enums import Position, UserStatus
from shared.config import settings
from shared.permissions import role_flags


def main_menu_kb(status: Optional[UserStatus], tg_id: int, position: Optional[Position] = None) -> ReplyKeyboardMarkup:
    """Build main menu keyboard based on user status and admin flag.
    - None or REJECTED -> show Register
    - PENDING, APPROVED, BLACKLISTED -> show Profile
    - If user is admin (tg_id in settings.admin_ids) -> add Employees button
    - Show "Закупки" for admins and approved users only
    Arrange buttons 2 per row; add emojis at start of labels.
    """
    buttons: list[KeyboardButton] = []
    if status in (UserStatus.PENDING, UserStatus.APPROVED, UserStatus.BLACKLISTED):
        buttons.append(KeyboardButton(text="🧾 Профиль"))
    else:
        buttons.append(KeyboardButton(text="📝 Зарегистрироваться"))

    try:
        r = role_flags(tg_id=int(tg_id), admin_ids=settings.admin_ids, status=status, position=position)
        is_admin_or_manager = bool(r.is_admin or r.is_manager)
        if is_admin_or_manager:
            buttons.append(KeyboardButton(text="🛠 Админ-панель"))

        if is_admin_or_manager or status == UserStatus.APPROVED:
            buttons.append(KeyboardButton(text="🛒 Закупки"))
            buttons.append(KeyboardButton(text="✅ Задачи"))
            buttons.append(KeyboardButton(text="📅 График работы"))

        can_stocks = is_admin_or_manager or (status == UserStatus.APPROVED and position in {Position.MASTER})
        if can_stocks:
            buttons.append(KeyboardButton(text="📦 Остатки"))

        can_reports = is_admin_or_manager
        if can_reports:
            buttons.append(KeyboardButton(text="📊 Отчёты и напоминания"))
    except Exception:
        pass

    # Arrange 2 per row
    rows: list[list[KeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i+2])

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
