from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from shared.enums import UserStatus


def build_main_keyboard(user, is_admin: bool) -> ReplyKeyboardMarkup:
    buttons = []
    if user is None:
        buttons.append([KeyboardButton(text="Зарегистрироваться")])
    else:
        status = user.status
        if status == UserStatus.APPROVED:
            buttons.append([KeyboardButton(text="Профиль")])
        elif status == UserStatus.PENDING:
            buttons.append([KeyboardButton(text="Статус заявки")])
            buttons.append([KeyboardButton(text="Зарегистрироваться")])
        elif status == UserStatus.REJECTED:
            buttons.append([KeyboardButton(text="Зарегистрироваться")])
        elif status == UserStatus.BLACKLISTED:
            pass
    if is_admin:
        buttons.append([KeyboardButton(text="Сотрудники")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def main_menu(is_admin: bool, approved: bool) -> ReplyKeyboardMarkup:
    user = None
    if approved:
        class _U:
            def __init__(self):
                self.status = UserStatus.APPROVED
        user = _U()
    return build_main_keyboard(user, is_admin)
