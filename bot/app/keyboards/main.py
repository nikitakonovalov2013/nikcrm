from typing import Optional
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from shared.enums import UserStatus
from shared.config import settings


def main_menu_kb(status: Optional[UserStatus], tg_id: int) -> ReplyKeyboardMarkup:
    """Build main menu keyboard based on user status and admin flag.
    - None or REJECTED -> show Register
    - PENDING, APPROVED, BLACKLISTED -> show Profile
    - If user is admin (tg_id in settings.admin_ids) -> add Employees button
    """
    rows: list[list[KeyboardButton]] = []
    if status in (UserStatus.PENDING, UserStatus.APPROVED, UserStatus.BLACKLISTED):
        rows.append([KeyboardButton(text="Профиль")])
    else:
        rows.append([KeyboardButton(text="Зарегистрироваться")])

    try:
        if tg_id in settings.admin_ids:
            rows.append([KeyboardButton(text="Сотрудники")])
    except Exception:
        # if settings not loaded properly, ignore admin button
        pass

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)
