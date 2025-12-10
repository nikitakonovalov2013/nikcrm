from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu(is_admin: bool, approved: bool) -> ReplyKeyboardMarkup:
    buttons = []
    if not approved:
        buttons.append([KeyboardButton(text="Зарегистрироваться")])
    else:
        buttons.append([KeyboardButton(text="Профиль")])
    if is_admin:
        buttons.append([KeyboardButton(text="Сотрудники")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)
