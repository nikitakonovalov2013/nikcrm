from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, InputMediaPhoto


async def render_tasks_screen(
    *,
    bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    state,
    photo: str | None = None,
) -> tuple[int, bool]:
    data = await state.get_data()
    message_id = data.get("tasks_message_id")
    has_media = bool(data.get("tasks_has_media"))

    if photo:
        if message_id and has_media:
            try:
                await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=int(message_id),
                    media=InputMediaPhoto(media=str(photo), caption=text, parse_mode="HTML"),
                    reply_markup=reply_markup,
                )
                return int(message_id), True
            except Exception:
                pass

        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=str(photo),
                caption=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            if message_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=int(message_id))
                except Exception:
                    pass
            await state.update_data(tasks_message_id=int(sent.message_id), tasks_chat_id=int(chat_id), tasks_has_media=True)
            return int(sent.message_id), True
        except Exception:
            sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup)
            await state.update_data(tasks_message_id=int(sent.message_id), tasks_chat_id=int(chat_id), tasks_has_media=False)
            return int(sent.message_id), False

    if message_id and not has_media:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
            return int(message_id), False
        except Exception:
            pass

    try:
        sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=reply_markup)
        if message_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=int(message_id))
            except Exception:
                pass
        await state.update_data(tasks_message_id=int(sent.message_id), tasks_chat_id=int(chat_id), tasks_has_media=False)
        return int(sent.message_id), False
    except Exception:
        return int(message_id or 0), False
