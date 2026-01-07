from __future__ import annotations

from aiogram.types import CallbackQuery, Message


def get_tg_user_id(event: Message | CallbackQuery) -> int:
    if isinstance(event, CallbackQuery):
        return int(event.from_user.id)
    return int(event.from_user.id)


def extract_tg_id(event: Message | CallbackQuery) -> int:
    return get_tg_user_id(event)
