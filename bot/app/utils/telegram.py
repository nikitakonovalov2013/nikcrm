from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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


async def send_new_and_delete_active(
    *,
    message: Message,
    state,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
    sync_to_tasks_screen: bool = False,
) -> Message | None:
    data = await state.get_data()
    chat_id = int(data.get("active_bot_chat_id") or message.chat.id)
    prev_id = data.get("active_bot_message_id")
    if prev_id:
        try:
            await message.bot.delete_message(chat_id=chat_id, message_id=int(prev_id))
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        except Exception:
            pass

    sent = await message.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    await state.update_data(active_bot_chat_id=int(chat_id), active_bot_message_id=int(sent.message_id))

    if sync_to_tasks_screen:
        await state.update_data(
            tasks_root_message_id=int(sent.message_id),
            tasks_root_chat_id=int(chat_id),
            tasks_root_has_media=False,
            tasks_message_id=int(sent.message_id),
            tasks_chat_id=int(chat_id),
            tasks_has_media=False,
        )
    return sent
