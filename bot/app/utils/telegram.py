from __future__ import annotations

from aiogram.types import Message, CallbackQuery


async def send_html(message: Message, text: str, reply_markup=None):
    return await message.answer(text, reply_markup=reply_markup)


async def edit_html(cb: CallbackQuery, text: str, reply_markup=None):
    try:
        return await cb.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        return await cb.message.answer(text, reply_markup=reply_markup)


async def edit_html_by_id(cb: CallbackQuery, *, chat_id: int, message_id: int, text: str, reply_markup=None):
    try:
        return await cb.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=reply_markup)
    except Exception:
        try:
            return await cb.message.answer(text, reply_markup=reply_markup)
        except Exception:
            return None


async def edit_html_by_id_from_message(message: Message, *, chat_id: int, message_id: int, text: str, reply_markup=None):
    try:
        return await message.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except Exception:
        try:
            return await message.answer(text, reply_markup=reply_markup)
        except Exception:
            return None
