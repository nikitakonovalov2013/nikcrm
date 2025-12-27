from typing import Optional
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from shared.enums import UserStatus
from shared.config import settings


def main_menu_kb(status: Optional[UserStatus], tg_id: int) -> ReplyKeyboardMarkup:
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
        is_admin = tg_id in settings.admin_ids
        if is_admin:
            buttons.append(KeyboardButton(text="🛠 Админ-панель"))
        if is_admin or status == UserStatus.APPROVED:
            buttons.append(KeyboardButton(text="🛒 Закупки"))
        if is_admin or status == UserStatus.APPROVED:
            buttons.append(KeyboardButton(text="📦 Остатки"))
        if is_admin or status == UserStatus.APPROVED:
            buttons.append(KeyboardButton(text="📊 Отчёты и напоминания"))
    except Exception:
        pass

    # Arrange 2 per row
    rows: list[list[KeyboardButton]] = []
    for i in range(0, len(buttons), 2):
        rows.append(buttons[i:i+2])

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
