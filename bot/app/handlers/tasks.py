from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from shared.config import settings
from shared.db import get_async_session
from shared.enums import TaskStatus, UserStatus
from shared.permissions import role_flags

from bot.app.keyboards.main import main_menu_kb
from bot.app.keyboards.tasks import (
    tasks_root_kb,
    tasks_list_kb,
    task_detail_kb,
    tasks_skip_photos_kb,
    tasks_text_cancel_kb,
)
from bot.app.repository.tasks import TaskRepository
from bot.app.services.tasks import TasksService
from bot.app.states.tasks import TasksState
from bot.app.utils.telegram import edit_html, send_html
from bot.app.utils.html import esc
from bot.app.utils.tg_id import get_tg_user_id
from bot.app.guards.user_guard import ensure_registered_or_reply
from bot.app.utils.tasks_screen import render_tasks_screen


router = Router()
_logger = logging.getLogger(__name__)

LIST_LIMIT = 12


async def _preserve_tasks_ui_and_clear_state(state: FSMContext) -> None:
    data = await state.get_data()
    keep = {
        k: data.get(k)
        for k in (
            "tasks_chat_id",
            "tasks_message_id",
            "tasks_has_media",
            "tasks_list_kind",
            "tasks_list_page",
        )
        if k in data
    }
    await state.clear()
    if keep:
        await state.update_data(**keep)


async def _get_service(tg_id: int) -> tuple[TasksService | None, object | None]:
    async with get_async_session() as session:
        repo = TaskRepository(session)
        svc = TasksService(repo)
        actor = await svc.get_actor_or_none(tg_id)
        return svc, actor


def _deny_text(note: str = "⛔ Нет доступа") -> str:
    return f"{note}."


async def _ensure_user(message: Message):
    return await ensure_registered_or_reply(message)


async def _render_menu(*, tg_id: int) -> tuple[str, object | None]:
    async with get_async_session() as session:
        repo = TaskRepository(session)
        actor = await repo.get_user_by_tg_id_any(int(tg_id))
        if not actor:
            return "ℹ️ Вы не зарегистрированы.", None

        if bool(getattr(actor, "is_deleted", False)):
            return "🚫 Ваш аккаунт помечен как удалённый/неактивный.", None

        r = role_flags(
            tg_id=tg_id,
            admin_ids=settings.admin_ids,
            status=actor.status,
            position=actor.position,
        )
        can_view_archive = bool(r.is_admin or r.is_manager)
        can_view_all = bool(r.is_admin or r.is_manager)

    text = "✅ <b>Задачи</b>\n\nВыберите список:" 
    kb = tasks_root_kb(can_view_archive=can_view_archive, can_view_all=can_view_all)
    return text, kb


@router.message(F.text.in_({"✅ Задачи", "Задачи"}))
@router.message(Command("tasks"))
async def tasks_entry(message: Message, state: FSMContext):
    actor = await _ensure_user(message)
    if not actor:
        return

    await _preserve_tasks_ui_and_clear_state(state)
    await state.update_data(tasks_chat_id=int(message.chat.id))
    text, kb = await _render_menu(tg_id=get_tg_user_id(message))
    await render_tasks_screen(
        bot=message.bot,
        chat_id=int(message.chat.id),
        text=text,
        reply_markup=kb,
        state=state,
        photo=None,
    )


async def _edit_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await _preserve_tasks_ui_and_clear_state(state)
    text, kb = await _render_menu(tg_id=get_tg_user_id(cb))
    await state.update_data(tasks_chat_id=int(cb.message.chat.id))
    await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        text=text,
        reply_markup=kb,
        state=state,
        photo=None,
    )


async def _show_list_by_id(
    message: Message,
    state: FSMContext,
    *,
    kind: str,
    page: int,
    actor_tg_id: int | None = None,
) -> None:
    data = await state.get_data()
    chat_id = data.get("tasks_chat_id")
    msg_id = data.get("tasks_message_id")
    if not chat_id or not msg_id:
        chat_id = int(chat_id or message.chat.id)
        await state.update_data(tasks_chat_id=int(chat_id))
        await render_tasks_screen(
            bot=message.bot,
            chat_id=int(chat_id),
            text="Загрузка…",
            reply_markup=None,
            state=state,
            photo=None,
        )
        data = await state.get_data()
        msg_id = data.get("tasks_message_id")

    async with get_async_session() as session:
        repo = TaskRepository(session)
        svc = TasksService(repo)
        tg_id = int(actor_tg_id) if actor_tg_id is not None else int(get_tg_user_id(message))
        actor, tasks, has_prev, has_next = await svc.list_for_actor(
            tg_id=tg_id,
            kind=kind,
            page=page,
            limit=LIST_LIMIT,
        )
        if not actor:
            await ensure_registered_or_reply(message)
            return

        title = svc.render_task_list_title(kind)
        if not tasks:
            text = f"{title}\n\nПока нет задач."
            kb = tasks_list_kb(kind=kind, page=page, items=[], has_prev=has_prev, has_next=has_next)
            await render_tasks_screen(
                bot=message.bot,
                chat_id=int(chat_id),
                text=text,
                reply_markup=kb,
                state=state,
                photo=None,
            )
            return

        items: list[tuple[int, str]] = []
        for t in tasks:
            items.append((int(t.id), svc.render_task_button_title(t)))

        text = f"{title}\n\nВыберите задачу:" 
        kb = tasks_list_kb(kind=kind, page=page, items=items, has_prev=has_prev, has_next=has_next)
        await state.update_data(tasks_list_kind=str(kind), tasks_list_page=int(page))
        await render_tasks_screen(
            bot=message.bot,
            chat_id=int(chat_id),
            text=text,
            reply_markup=kb,
            state=state,
            photo=None,
        )


@router.callback_query(F.data == "tasks:menu")
async def cb_tasks_menu(cb: CallbackQuery, state: FSMContext):
    await _edit_menu(cb, state)


@router.callback_query(F.data.startswith("tasks:list:"))
async def cb_tasks_list(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 4:
        await cb.answer("Ошибка")
        return
    _, _, kind, page_s = parts
    try:
        page = int(page_s)
    except Exception:
        page = 0

    await cb.answer()

    user = await ensure_registered_or_reply(cb)
    if not user:
        return

    # reuse message editing
    fake_msg = cb.message
    if fake_msg is None:
        return
    await _show_list_by_id(fake_msg, state, kind=kind, page=page, actor_tg_id=int(cb.from_user.id))


@router.callback_query(F.data.startswith("tasks:open:"))
async def cb_tasks_open(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 5:
        await cb.answer("Ошибка")
        return
    _, _, task_id_s, kind, page_s = parts
    try:
        task_id = int(task_id_s)
    except Exception:
        await cb.answer("Ошибка")
        return

    await cb.answer()
    try:
        await state.update_data(tasks_list_kind=str(kind), tasks_list_page=int(page_s))
    except Exception:
        pass
    await _show_task_detail(cb, state, task_id=task_id)


@router.callback_query(F.data.startswith("tasks:open_notify:"))
async def cb_tasks_open_notify(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer("Ошибка")
        return
    try:
        task_id = int(parts[2])
    except Exception:
        await cb.answer("Ошибка")
        return

    await cb.answer()
    if not cb.message:
        return

    # Treat the notification message as the main tasks UI message to keep single-message behavior.
    has_media = bool(getattr(cb.message, "photo", None)) or bool(getattr(cb.message, "document", None))
    await state.update_data(
        tasks_chat_id=int(cb.message.chat.id),
        tasks_message_id=int(cb.message.message_id),
        tasks_has_media=bool(has_media),
    )
    await _show_task_detail(cb, state, task_id=task_id)


async def _show_task_detail(cb: CallbackQuery, state: FSMContext, *, task_id: int) -> None:
    async with get_async_session() as session:
        repo = TaskRepository(session)
        svc = TasksService(repo)
        actor, task, perms = await svc.get_detail(tg_id=cb.from_user.id, task_id=task_id)
        if not actor:
            await ensure_registered_or_reply(cb)
            return
        if not task:
            await edit_html(cb, "⚠️ Задача не найдена.")
            return

        data = await state.get_data()
        back_kind = data.get("tasks_list_kind")
        back_page = data.get("tasks_list_page")
        html = svc.render_task_detail_html(task, perms=perms)

        photo = getattr(task, "photo_file_id", None) or None

        kb = task_detail_kb(
            task_id=int(task.id),
            can_take=bool(perms.take_in_progress),
            can_to_review=bool(perms.finish_to_review),
            can_accept_done=bool(perms.accept_done),
            can_send_back=bool(perms.send_back),
            back_kind=str(back_kind) if back_kind is not None else None,
            back_page=int(back_page) if back_page is not None else None,
        )
        await state.update_data(tasks_chat_id=int(cb.message.chat.id))
        await render_tasks_screen(
            bot=cb.bot,
            chat_id=int(cb.message.chat.id),
            text=html,
            reply_markup=kb,
            state=state,
            photo=photo,
        )


@router.callback_query(F.data.startswith("tasks:status:"))
async def cb_tasks_status(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 4:
        await cb.answer("Ошибка")
        return
    _, _, task_id_s, to_status = parts
    try:
        task_id = int(task_id_s)
    except Exception:
        await cb.answer("Ошибка")
        return

    await cb.answer()

    async with get_async_session() as session:
        repo = TaskRepository(session)
        svc = TasksService(repo)
        ok, code = await svc.change_status(
            tg_id=cb.from_user.id,
            task_id=task_id,
            to_status=to_status,
        )
        if not ok:
            if code == "not_registered":
                await ensure_registered_or_reply(cb)
                return
            if code == "not_found":
                await render_tasks_screen(
                    bot=cb.bot,
                    chat_id=int(cb.message.chat.id),
                    text="⚠️ Задача не найдена.",
                    reply_markup=None,
                    state=state,
                    photo=None,
                )
                return
            if code == "forbidden":
                await render_tasks_screen(
                    bot=cb.bot,
                    chat_id=int(cb.message.chat.id),
                    text=_deny_text(),
                    reply_markup=None,
                    state=state,
                    photo=None,
                )
                return
            await render_tasks_screen(
                bot=cb.bot,
                chat_id=int(cb.message.chat.id),
                text="⚠️ Не удалось выполнить действие.",
                reply_markup=None,
                state=state,
                photo=None,
            )
            return

    await _show_task_detail(cb, state, task_id=task_id)


@router.callback_query(F.data.startswith("tasks:comment:"))
async def cb_tasks_comment(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer("Ошибка")
        return
    task_id = int(parts[2])

    await cb.answer()
    data = await state.get_data()
    chat_id = data.get("tasks_chat_id") or (cb.message.chat.id if cb.message else None)
    await _preserve_tasks_ui_and_clear_state(state)
    await state.set_state(TasksState.comment_text)
    await state.update_data(task_id=task_id, photos=[], tasks_chat_id=int(chat_id) if chat_id else None)
    await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(chat_id),
        text="💬 <b>Комментарий</b>\n\nВведите текст комментария (обязательно, либо фото на следующем шаге):",
        reply_markup=tasks_text_cancel_kb(task_id=task_id),
        state=state,
        photo=None,
    )


@router.callback_query(F.data.startswith("tasks:cancel_text:"))
async def cb_tasks_cancel_text(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer("Ошибка")
        return
    try:
        task_id = int(parts[2])
    except Exception:
        await cb.answer("Ошибка")
        return
    await cb.answer()
    await _preserve_tasks_ui_and_clear_state(state)
    await _show_task_detail(cb, state, task_id=task_id)


@router.message(TasksState.comment_text)
async def st_comment_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    await state.update_data(comment_text=text)

    kb = tasks_skip_photos_kb(allow_done=True)
    await state.set_state(TasksState.comment_photos)
    data = await state.get_data()
    chat_id = int(data.get("tasks_chat_id") or message.chat.id)
    await render_tasks_screen(
        bot=message.bot,
        chat_id=chat_id,
        text="📷 <b>Фото (опционально)</b>\n\nПришлите фото по одному сообщению.\nНажмите <b>Готово</b>, когда закончите, или <b>Пропустить</b>.",
        reply_markup=kb,
        state=state,
        photo=None,
    )


@router.message(TasksState.comment_photos, F.photo)
async def st_comment_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos: list[str] = list(data.get("photos") or [])
    try:
        fid = message.photo[-1].file_id
        photos.append(fid)
        await state.update_data(photos=photos)
    except Exception:
        pass


@router.callback_query(TasksState.comment_photos, F.data.in_({"tasks:comment_done", "tasks:comment_skip"}))
async def st_comment_finish(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    task_id = int(data.get("task_id") or 0)
    text = (data.get("comment_text") or "").strip()
    photos: list[str] = list(data.get("photos") or [])

    await cb.answer()

    if not text and not photos:
        await render_tasks_screen(
            bot=cb.bot,
            chat_id=int(cb.message.chat.id),
            text="⚠️ Нужен текст или хотя бы одно фото.",
            reply_markup=None,
            state=state,
            photo=None,
        )
        return

    async with get_async_session() as session:
        repo = TaskRepository(session)
        svc = TasksService(repo)
        ok = await svc.add_comment(tg_id=cb.from_user.id, task_id=task_id, text=text or None, photo_file_ids=photos)
        if not ok:
            await render_tasks_screen(
                bot=cb.bot,
                chat_id=int(cb.message.chat.id),
                text="⚠️ Не удалось добавить комментарий.",
                reply_markup=None,
                state=state,
                photo=None,
            )
            return

    await _preserve_tasks_ui_and_clear_state(state)
    await state.update_data(tasks_chat_id=int(cb.message.chat.id))
    await _show_task_detail(cb, state, task_id=task_id)


@router.callback_query(TasksState.comment_photos, F.data == "tasks:comment_cancel")
async def st_comment_cancel(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    task_id = int(data.get("task_id") or 0)
    await cb.answer()
    await _preserve_tasks_ui_and_clear_state(state)
    await _show_task_detail(cb, state, task_id=task_id)


@router.callback_query(F.data.startswith("tasks:rework:"))
async def cb_tasks_rework(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer("Ошибка")
        return
    task_id = int(parts[2])
    await cb.answer()

    data = await state.get_data()
    chat_id = data.get("tasks_chat_id") or (cb.message.chat.id if cb.message else None)
    await state.set_state(TasksState.rework_text)
    await state.update_data(task_id=task_id, tasks_chat_id=int(chat_id) if chat_id else None)
    await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(chat_id),
        text="↩️ <b>На доработку</b>\n\nВведите комментарий (обязательно):",
        reply_markup=tasks_text_cancel_kb(task_id=task_id),
        state=state,
        photo=None,
    )


@router.message(TasksState.rework_text)
async def st_rework_text(message: Message, state: FSMContext):
    comment = (message.text or "").strip()
    if not comment:
        await message.answer("⚠️ Комментарий обязателен. Введите текст.")
        return

    data = await state.get_data()
    task_id = int(data.get("task_id") or 0)

    async with get_async_session() as session:
        repo = TaskRepository(session)
        svc = TasksService(repo)
        ok, code = await svc.change_status(
            tg_id=message.from_user.id,
            task_id=task_id,
            to_status=TaskStatus.IN_PROGRESS.value,
            comment=comment,
        )
        if not ok:
            if code == "comment_required":
                await message.answer("⚠️ Комментарий обязателен.")
                return
            if code == "forbidden":
                await message.answer(_deny_text())
                await _preserve_tasks_ui_and_clear_state(state)
                return
            await message.answer("⚠️ Не удалось отправить на доработку.")
            await _preserve_tasks_ui_and_clear_state(state)
            return

    data2 = await state.get_data()
    chat_id = int(data2.get("tasks_chat_id") or message.chat.id)

    async with get_async_session() as session2:
        repo2 = TaskRepository(session2)
        svc2 = TasksService(repo2)
        _, task2, perms2 = await svc2.get_detail(tg_id=message.from_user.id, task_id=task_id)
        if task2 and perms2:
            back_kind = data2.get("tasks_list_kind")
            back_page = data2.get("tasks_list_page")
            html = svc2.render_task_detail_html(task2, perms=perms2) + "\n\n✅ Отправлено на доработку."

            photo = getattr(task2, "photo_file_id", None) or None

            kb = task_detail_kb(
                task_id=int(task2.id),
                can_take=bool(perms2.take_in_progress),
                can_to_review=bool(perms2.finish_to_review),
                can_accept_done=bool(perms2.accept_done),
                can_send_back=bool(perms2.send_back),
                back_kind=str(back_kind) if back_kind is not None else None,
                back_page=int(back_page) if back_page is not None else None,
            )
            await _preserve_tasks_ui_and_clear_state(state)
            await state.update_data(tasks_chat_id=int(chat_id))
            await render_tasks_screen(
                bot=message.bot,
                chat_id=int(chat_id),
                text=html,
                reply_markup=kb,
                state=state,
                photo=photo,
            )
            return

    await _preserve_tasks_ui_and_clear_state(state)
    await render_tasks_screen(
        bot=message.bot,
        chat_id=int(chat_id),
        text=f"✅ Отправлено на доработку: задача #{task_id}.",
        reply_markup=None,
        state=state,
        photo=None,
    )
