from __future__ import annotations

import logging
import calendar
from datetime import datetime, timedelta, timezone

import httpx

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup, BufferedInputFile

from shared.config import settings
from shared.db import get_async_session
from shared.enums import TaskStatus, TaskPriority, UserStatus
from shared.permissions import role_flags
from shared.utils import MOSCOW_TZ

from bot.app.keyboards.main import main_menu_kb
from bot.app.keyboards.tasks import (
    tasks_root_kb,
    tasks_status_kb,
    tasks_list_kb,
    task_detail_kb,
    tasks_skip_photos_kb,
    tasks_text_cancel_kb,
    tasks_create_cancel_kb,
    tasks_create_desc_kb,
    tasks_create_photo_kb,
    tasks_create_priority_kb,
    tasks_create_due_kb,
    tasks_create_assignees_kb,
    tasks_create_confirm_kb,
)
from bot.app.repository.tasks import TaskRepository
from bot.app.services.tasks import TasksService
from bot.app.states.tasks import TasksState
from bot.app.utils.telegram import edit_html, send_html, send_new_and_delete_active
from bot.app.utils.html import esc
from bot.app.utils.tg_id import get_tg_user_id
from bot.app.guards.user_guard import ensure_registered_or_reply
from bot.app.utils.tasks_screen import render_tasks_screen
from bot.app.utils.urls import build_task_board_magic_link, build_tasks_board_magic_link


router = Router()
_logger = logging.getLogger(__name__)

LIST_LIMIT = 12


def _public_base_url() -> str:
    raw = str(getattr(settings, "PUBLIC_BASE_URL", "") or "").strip()
    if not raw:
        raw = str(getattr(settings, "APP_URL", "") or "").strip()
    if not raw:
        raw = str(getattr(settings, "BASE_URL", "") or "").strip()
    if not raw:
        raw = str(getattr(settings, "admin_panel_url", "") or "").strip()
    if not raw:
        return ""
    if raw.endswith("/"):
        raw = raw[:-1]
    if raw.endswith("/crm"):
        raw = raw[: -len("/crm")]
    return raw


def _web_internal_base_url() -> str:
    raw = str(getattr(settings, "WEB_INTERNAL_BASE_URL", "") or "").strip()
    if not raw:
        raw = str(getattr(settings, "INTERNAL_WEB_BASE_URL", "") or "").strip()
    if not raw:
        return ""
    if raw.endswith("/"):
        raw = raw[:-1]
    if raw.endswith("/crm"):
        raw = raw[: -len("/crm")]
    return raw


def _build_web_download_url(*, photo_url: str | None, photo_path: str | None) -> str | None:
    # Prefer internal download by path (works in docker even if public URL is 127.0.0.1)
    internal = _web_internal_base_url()
    path = str(photo_path or "").strip()
    if internal and path:
        if not path.startswith("/"):
            path = "/" + path
        return internal + path

    url = str(photo_url or "").strip()
    if not url:
        return None
    # If public URL points to localhost/127.0.0.1, try to map it to internal base
    if internal and ("//127.0.0.1" in url or "//localhost" in url):
        try:
            # Keep only path/query part
            from urllib.parse import urlsplit

            u = urlsplit(url)
            p = u.path or ""
            q = ("?" + u.query) if u.query else ""
            if p:
                return internal + p + q
        except Exception:
            pass
    return url


async def _download_tg_file_bytes(*, bot, file_id: str) -> tuple[bytes | None, str | None]:
    try:
        f = await bot.get_file(str(file_id))
        file_path = getattr(f, "file_path", None)
        if not file_path:
            return None, None
    except Exception:
        return None, None

    url = f"https://api.telegram.org/file/bot{settings.BOT_TOKEN}/{file_path}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None, None
            data = bytes(r.content)
            name = str(file_path).split("/")[-1] if file_path else None
            return data, name
    except Exception:
        return None, None


async def _upload_task_photo_to_web(*, task_id: int, tg_file_id: str, bot) -> None:
    # Deprecated by iron-clad strategy (photo_key as canonical). Keep for compatibility.
    return


async def _upload_tg_photo_to_web_storage(*, tg_file_id: str, bot) -> dict | None:
    data, name = await _download_tg_file_bytes(bot=bot, file_id=str(tg_file_id))
    if not data:
        return None

    base = str(getattr(settings, "INTERNAL_WEB_BASE_URL", "") or "").rstrip("/")
    token = str(getattr(settings, "INTERNAL_API_TOKEN", "") or "")
    if not base or not token:
        return None

    url = base + "/crm/api/internal/tasks/upload-photo"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            files = {"photo": (name or "photo.jpg", data, "image/jpeg")}
            resp = await client.post(url, files=files, headers={"X-Internal-Token": token})
            if resp.status_code != 200:
                return None
            return dict(resp.json() or {})
    except Exception:
        return None


def _due_from_preset(preset: str) -> datetime | None:
    p = str(preset)
    if p == "none":
        return None
    now_msk = datetime.now(MOSCOW_TZ)
    if p == "today18":
        dt = now_msk.replace(hour=18, minute=0, second=0, microsecond=0)
        return dt.astimezone(timezone.utc)
    if p == "today21":
        dt = now_msk.replace(hour=21, minute=0, second=0, microsecond=0)
        return dt.astimezone(timezone.utc)
    if p == "tomorrow18":
        dt = (now_msk + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        return dt.astimezone(timezone.utc)
    if p == "tomorrow21":
        dt = (now_msk + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
        return dt.astimezone(timezone.utc)
    if p == "eow":
        # End of week = ближайшее воскресенье, 21:00 (MSK)
        days_until_sun = 6 - int(now_msk.weekday())
        dt = (now_msk + timedelta(days=days_until_sun)).replace(hour=21, minute=0, second=0, microsecond=0)
        return dt.astimezone(timezone.utc)
    if p == "eom":
        # End of month = последний день месяца, 21:00 (MSK)
        y = int(now_msk.year)
        m = int(now_msk.month)
        last_day = int(calendar.monthrange(y, m)[1])
        dt = now_msk.replace(day=last_day, hour=21, minute=0, second=0, microsecond=0)
        return dt.astimezone(timezone.utc)
    return None


async def advance_step_with_new_bot_message(
    *,
    bot,
    chat_id: int,
    state: FSMContext,
    text: str,
    reply_markup=None,
    parse_mode: str = "HTML",
    sync_to_tasks_screen: bool = True,
) -> int | None:
    data = await state.get_data()
    prev_chat_id = int(data.get("active_bot_chat_id") or chat_id)
    prev_id = data.get("active_bot_message_id")
    if prev_id:
        try:
            await bot.delete_message(chat_id=int(prev_chat_id), message_id=int(prev_id))
        except Exception:
            pass

    sent = await bot.send_message(
        chat_id=int(chat_id),
        text=str(text),
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
    return int(sent.message_id)


def _truncate_name(s: str, n: int = 28) -> str:
    v = (s or "").strip()
    if len(v) <= n:
        return v
    return v[: n - 1] + "…"


def _truncate_text(s: str | None, n: int) -> str:
    v = (s or "").strip()
    if not v:
        return "—"
    if len(v) <= n:
        return v
    return v[: n - 1] + "…"


def render_task_draft(state_data: dict, *, full: bool = False) -> str:
    title_n = 120 if full else 90
    desc_n = 240 if full else 140

    title = _truncate_text(state_data.get("draft_title"), title_n)
    desc = _truncate_text(state_data.get("draft_description"), desc_n)

    photo = "✅" if state_data.get("draft_photo_file_id") else "—"

    pr = state_data.get("draft_priority")
    if pr == TaskPriority.URGENT.value:
        pr_s = "🔥 Срочная"
    elif pr == TaskPriority.NORMAL.value:
        pr_s = "Обычная"
    else:
        pr_s = "—"

    due_s = "—"
    if "draft_due_at" in state_data:
        due_iso = state_data.get("draft_due_at")
        if not due_iso:
            due_s = "Без дедлайна"
        else:
            try:
                dt = datetime.fromisoformat(str(due_iso))
                due_s = dt.astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
            except Exception:
                due_s = "—"

    assignee_ids = list(state_data.get("draft_assignee_ids") or [])
    assignee_names = list(state_data.get("draft_assignee_names") or [])
    if assignee_ids and assignee_names:
        if full:
            assignees_s = ", ".join(esc(str(x)) for x in assignee_names)
        else:
            assignees_s = ", ".join(esc(_truncate_name(str(x), 26)) for x in assignee_names[:5])
            if len(assignee_names) > 5:
                assignees_s += f" и ещё {len(assignee_names) - 5}"
    elif "draft_assignee_ids" in state_data:
        assignees_s = "Общая" if not assignee_ids else f"Выбрано: {len(assignee_ids)}"
    else:
        assignees_s = "—"

    lines: list[str] = []
    lines.append("📌 <b>Черновик задачи</b>")
    lines.append(f"<b>Название:</b> {esc(title) if title != '—' else '—'}")
    lines.append(f"<b>Описание:</b> {esc(desc) if desc != '—' else '—'}")
    lines.append(f"<b>Фото:</b> {photo}")
    lines.append(f"<b>Приоритет:</b> {pr_s}")
    lines.append(f"<b>Дедлайн:</b> {esc(due_s)}")
    lines.append(f"<b>Исполнители:</b> {assignees_s}")
    return "\n".join(lines)


def _with_draft(state_data: dict, step_text: str, *, full: bool = False) -> str:
    return render_task_draft(state_data, full=full) + "\n\n" + str(step_text)


async def _preserve_tasks_ui_and_clear_state(state: FSMContext) -> None:
    data = await state.get_data()
    keep = {
        k: data.get(k)
        for k in (
            "tasks_root_chat_id",
            "tasks_root_message_id",
            "tasks_root_has_media",
            "tasks_chat_id",
            "tasks_message_id",
            "tasks_has_media",
            "tasks_scope",
            "tasks_status",
            "tasks_page",
            "tasks_last_list_context",
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

        board_url = await build_tasks_board_magic_link(
            session=session,
            user=actor,
            is_admin=bool(r.is_admin),
            is_manager=bool(r.is_manager),
        )

    text = "✅ <b>Задачи</b>\n\nВыберите действие:\n\n" + f"🌐 Доска задач: <a href=\"{esc(board_url)}\">Открыть</a>"
    kb = tasks_root_kb(can_view_all=can_view_all)
    return text, kb


async def _get_role_flags(tg_id: int):
    async with get_async_session() as session:
        repo = TaskRepository(session)
        actor = await repo.get_user_by_tg_id_any(int(tg_id))
        if not actor:
            return None, None
        r = role_flags(
            tg_id=tg_id,
            admin_ids=settings.admin_ids,
            status=actor.status,
            position=actor.position,
        )
        return actor, r


async def _get_user(tg_id: int):
    async with get_async_session() as session:
        repo = TaskRepository(session)
        return await repo.get_user_by_tg_id_any(int(tg_id))


async def _deny_and_back_to_menu(cb: CallbackQuery, state: FSMContext, note: str = "⛔ Недостаточно прав") -> None:
    await cb.answer()
    await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        text=_deny_text(note),
        reply_markup=None,
        state=state,
        photo=None,
    )
    await _edit_menu(cb, state)


async def _show_status_menu(cb: CallbackQuery, state: FSMContext, *, scope: str) -> None:
    actor, r = await _get_role_flags(int(cb.from_user.id))
    if not actor:
        await ensure_registered_or_reply(cb)
        return

    can_view_all = bool(r.is_admin or r.is_manager)
    can_view_archive = bool(r.is_admin or r.is_manager)

    if scope == "all" and not can_view_all:
        await _deny_and_back_to_menu(cb, state)
        return

    await _preserve_tasks_ui_and_clear_state(state)
    await state.update_data(tasks_chat_id=int(cb.message.chat.id), tasks_scope=str(scope), tasks_page=0)

    title = "👤 <b>Мои задачи</b> — выберите статус" if scope == "mine" else "📋 <b>Все задачи</b> — выберите статус"
    kb = tasks_status_kb(scope=str(scope), can_view_archive=bool(can_view_archive))
    await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        text=title,
        reply_markup=kb,
        state=state,
        photo=None,
    )


def _status_to_enum_value(st: str) -> str:
    s = str(st)
    if s in {
        TaskStatus.NEW.value,
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.REVIEW.value,
        TaskStatus.DONE.value,
        TaskStatus.ARCHIVED.value,
    }:
        return s
    return TaskStatus.NEW.value


def _status_title_ru(st: str) -> str:
    return {
        TaskStatus.NEW.value: "Новые",
        TaskStatus.IN_PROGRESS.value: "В работе",
        TaskStatus.REVIEW.value: "На проверке",
        TaskStatus.DONE.value: "Выполнено",
        TaskStatus.ARCHIVED.value: "Архив",
    }.get(str(st), str(st))


async def _show_list_scope_status(
    cb: CallbackQuery,
    state: FSMContext,
    *,
    scope: str,
    status: str,
    page: int,
) -> None:
    actor, r = await _get_role_flags(int(cb.from_user.id))
    if not actor:
        await ensure_registered_or_reply(cb)
        return
    is_admin_or_manager = bool(r.is_admin or r.is_manager)
    if scope == "all" and not is_admin_or_manager:
        await _deny_and_back_to_menu(cb, state)
        return

    status = _status_to_enum_value(status)
    page = max(0, int(page))

    async with get_async_session() as session:
        repo = TaskRepository(session)
        tasks, has_prev, has_next = await repo.list_tasks_by_scope_status(
            scope=str(scope),
            status=str(status),
            actor_user_id=int(actor.id),
            is_admin_or_manager=bool(is_admin_or_manager),
            page=int(page),
            limit=LIST_LIMIT,
        )

    await state.update_data(
        tasks_scope=str(scope),
        tasks_status=str(status),
        tasks_page=int(page),
        tasks_last_list_context={"scope": str(scope), "status": str(status), "page": int(page)},
        tasks_list_kind=f"{scope}:{status}",
        tasks_list_page=int(page),
    )

    scope_title = "👤 <b>Мои задачи</b>" if scope == "mine" else "📋 <b>Все задачи</b>"
    title = f"{scope_title} · <b>{esc(_status_title_ru(status))}</b>"

    if not tasks:
        text = f"{title}\n\nНет задач в этом статусе."
        kb = tasks_list_kb(
            scope=str(scope),
            status=str(status),
            page=int(page),
            items=[],
            has_prev=bool(has_prev),
            has_next=bool(has_next),
        )
        await render_tasks_screen(
            bot=cb.bot,
            chat_id=int(cb.message.chat.id),
            text=text,
            reply_markup=kb,
            state=state,
            photo=None,
        )
        return

    async with get_async_session() as session2:
        repo2 = TaskRepository(session2)
        svc = TasksService(repo2)
        items: list[tuple[int, str]] = []
        for t in tasks:
            pr = t.priority.value if hasattr(t.priority, "value") else str(t.priority)
            urgent = " 🔥" if pr == "urgent" else ""
            title_short = esc(getattr(t, "title", "") or "")
            if len(title_short) > 42:
                title_short = title_short[:39] + "…"
            items.append((int(t.id), f"#{int(t.id)} · {title_short}{urgent}"))

    text = f"{title}\n\nВыберите задачу:"
    kb = tasks_list_kb(
        scope=str(scope),
        status=str(status),
        page=int(page),
        items=items,
        has_prev=bool(has_prev),
        has_next=bool(has_next),
    )
    await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        text=text,
        reply_markup=kb,
        state=state,
        photo=None,
    )


@router.message(F.text.in_({"✅ Задачи", "Задачи"}))
@router.message(Command("tasks"))
async def tasks_entry(message: Message, state: FSMContext):
    actor = await _ensure_user(message)
    if not actor:
        return

    data = await state.get_data()
    prev_chat_id = data.get("tasks_root_chat_id")
    prev_message_id = data.get("tasks_root_message_id")
    if prev_chat_id and prev_message_id:
        try:
            await message.bot.delete_message(chat_id=int(prev_chat_id), message_id=int(prev_message_id))
        except Exception:
            pass

    await state.clear()
    await state.update_data(tasks_chat_id=int(message.chat.id), tasks_root_chat_id=int(message.chat.id))

    text, kb = await _render_menu(tg_id=get_tg_user_id(message))
    sent = await message.bot.send_message(
        chat_id=int(message.chat.id),
        text=text,
        parse_mode="HTML",
        reply_markup=kb,
    )
    await state.update_data(
        tasks_root_message_id=int(sent.message_id),
        tasks_root_has_media=False,
        tasks_message_id=int(sent.message_id),
        tasks_has_media=False,
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


@router.callback_query(F.data == "tasks:menu")
async def cb_tasks_menu(cb: CallbackQuery, state: FSMContext):
    await _edit_menu(cb, state)


@router.callback_query(F.data == "tasks:back_main")
async def cb_tasks_back_main(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    user = await _get_user(cb.from_user.id)
    status = None
    position = None
    if user:
        status = user.status
        position = user.position

    data = await state.get_data()
    chat_id = int((data.get("tasks_root_chat_id") or data.get("tasks_chat_id") or cb.message.chat.id))
    msg_id = data.get("tasks_root_message_id") or data.get("tasks_message_id")
    await state.clear()

    if msg_id:
        try:
            await cb.bot.delete_message(chat_id=chat_id, message_id=int(msg_id))
        except Exception:
            pass

    await cb.message.answer("Выберите действие ниже.", reply_markup=main_menu_kb(status, cb.from_user.id, position))


@router.callback_query(F.data == "tasks:mine")
async def cb_tasks_mine(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await _show_status_menu(cb, state, scope="mine")


@router.callback_query(F.data == "tasks:all")
async def cb_tasks_all(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await _show_status_menu(cb, state, scope="all")


@router.callback_query(F.data == "tasks:new")
async def cb_tasks_new(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    actor = await ensure_registered_or_reply(cb)
    if not actor:
        return
    await _preserve_tasks_ui_and_clear_state(state)
    await state.set_state(TasksState.create_title)
    await state.update_data(
        tasks_chat_id=int(cb.message.chat.id),
        draft_title=None,
        draft_description=None,
        draft_photo_file_id=None,
        draft_priority=None,
        draft_due_at=None,
        draft_assignee_ids=[],
        draft_assignee_names=[],
        draft_assignee_map={},
        create_assignees_page=0,
    )
    data0 = await state.get_data()
    mid, _has_media = await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        text=_with_draft(data0, "➕ <b>Новая задача</b>\n\nВведите заголовок:"),
        reply_markup=tasks_create_cancel_kb(),
        state=state,
        photo=None,
    )
    await state.update_data(active_bot_chat_id=int(cb.message.chat.id), active_bot_message_id=int(mid) if mid else None)


@router.callback_query(F.data == "tasks:create_cancel")
async def cb_tasks_create_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await _preserve_tasks_ui_and_clear_state(state)
    await _edit_menu(cb, state)


@router.message(TasksState.create_title)
async def st_tasks_create_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title:
        return
    await state.update_data(draft_title=title)
    await state.set_state(TasksState.create_description)
    data = await state.get_data()
    await send_new_and_delete_active(
        message=message,
        state=state,
        text=_with_draft(data, "📝 <b>Описание</b>\n\nВведите описание или нажмите <b>Пропустить</b>."),
        reply_markup=tasks_create_desc_kb(),
        sync_to_tasks_screen=True,
    )


@router.callback_query(TasksState.create_description, F.data == "tasks:create_desc_skip")
async def cb_tasks_create_desc_skip(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(draft_description=None)
    await state.set_state(TasksState.create_photo)
    data = await state.get_data()
    mid, _has_media = await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        text=_with_draft(data, "🖼️ <b>Фото задачи</b>\n\nПришлите фото (как изображение) или нажмите <b>Пропустить</b>."),
        reply_markup=tasks_create_photo_kb(),
        state=state,
        photo=None,
    )
    await state.update_data(active_bot_chat_id=int(cb.message.chat.id), active_bot_message_id=int(mid) if mid else None)


@router.message(TasksState.create_description)
async def st_tasks_create_description(message: Message, state: FSMContext):
    desc = (message.text or "").strip()
    await state.update_data(draft_description=(desc or None))
    await state.set_state(TasksState.create_photo)
    data = await state.get_data()
    await send_new_and_delete_active(
        message=message,
        state=state,
        text=_with_draft(data, "🖼️ <b>Фото задачи</b>\n\nПришлите фото (как изображение) или нажмите <b>Пропустить</b>."),
        reply_markup=tasks_create_photo_kb(),
        sync_to_tasks_screen=True,
    )


@router.callback_query(TasksState.create_photo, F.data == "tasks:create_photo_skip")
async def cb_tasks_create_photo_skip(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(draft_photo_file_id=None)
    await state.set_state(TasksState.create_priority)
    data = await state.get_data()
    await advance_step_with_new_bot_message(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        state=state,
        text=_with_draft(data, "⚙️ <b>Приоритет</b>\n\nВыберите приоритет задачи:"),
        reply_markup=tasks_create_priority_kb(),
        sync_to_tasks_screen=True,
    )


@router.message(TasksState.create_photo, F.photo)
async def st_tasks_create_photo(message: Message, state: FSMContext):
    try:
        fid = message.photo[-1].file_id
    except Exception:
        fid = None
    await state.update_data(draft_photo_file_id=(str(fid) if fid else None))
    await state.set_state(TasksState.create_priority)
    data = await state.get_data()
    await advance_step_with_new_bot_message(
        bot=message.bot,
        chat_id=int(message.chat.id),
        state=state,
        text=_with_draft(data, "⚙️ <b>Приоритет</b>\n\nВыберите приоритет задачи:"),
        reply_markup=tasks_create_priority_kb(),
        sync_to_tasks_screen=True,
    )


@router.message(TasksState.create_photo)
async def st_tasks_create_photo_invalid(message: Message, state: FSMContext):
    data = await state.get_data()
    await render_tasks_screen(
        bot=message.bot,
        chat_id=int(message.chat.id),
        text=_with_draft(data, "⚠️ Пришлите фото (как изображение) или нажмите <b>Пропустить</b>."),
        reply_markup=tasks_create_photo_kb(),
        state=state,
        photo=None,
    )


@router.callback_query(TasksState.create_priority, F.data.startswith("tasks:create_priority:"))
async def cb_tasks_create_priority(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    parts = cb.data.split(":")
    if len(parts) != 3:
        return
    val = parts[2]
    pr = TaskPriority.URGENT.value if val == TaskPriority.URGENT.value else TaskPriority.NORMAL.value
    await state.update_data(draft_priority=str(pr))
    await state.set_state(TasksState.create_due)
    data = await state.get_data()
    await advance_step_with_new_bot_message(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        state=state,
        text=_with_draft(data, "⏰ <b>Дедлайн</b>\n\nВыберите дедлайн:"),
        reply_markup=tasks_create_due_kb(),
        sync_to_tasks_screen=True,
    )


@router.callback_query(TasksState.create_due, F.data == "tasks:create_back_priority")
async def cb_tasks_create_back_priority(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(TasksState.create_priority)
    data = await state.get_data()
    await advance_step_with_new_bot_message(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        state=state,
        text=_with_draft(data, "⚙️ <b>Приоритет</b>\n\nВыберите приоритет задачи:"),
        reply_markup=tasks_create_priority_kb(),
        sync_to_tasks_screen=True,
    )


async def _render_create_assignees(cb: CallbackQuery, state: FSMContext, *, page: int) -> None:
    data = await state.get_data()
    selected = set(int(x) for x in (data.get("draft_assignee_ids") or []))
    page = max(0, int(page))
    limit = 16
    offset = page * limit

    async with get_async_session() as session:
        repo = TaskRepository(session)
        users = await repo.list_assignable_users()

    total = len(users)
    slice_users = users[offset : offset + limit]
    has_prev = page > 0
    has_next = (offset + limit) < total

    items: list[tuple[int, str]] = []
    name_map: dict[int, str] = {}
    for u in slice_users:
        name = ((f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip()) or f"#{int(u.id)}")
        name_map[int(u.id)] = str(name)
        items.append((int(u.id), _truncate_name(name)))

    merged_map = dict(data.get("draft_assignee_map") or {})
    for k, v in name_map.items():
        merged_map[int(k)] = str(v)
    selected_names = [merged_map.get(int(uid)) for uid in sorted(selected) if merged_map.get(int(uid))]
    await state.update_data(draft_assignee_map=merged_map, draft_assignee_names=selected_names)

    data2 = await state.get_data()
    text = _with_draft(data2, "👥 <b>Исполнители</b>\n\nВыберите исполнителей (можно никого):")
    kb = tasks_create_assignees_kb(users=items, selected_ids=selected, page=page, has_prev=has_prev, has_next=has_next)
    await state.update_data(create_assignees_page=int(page))
    await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        text=text,
        reply_markup=kb,
        state=state,
        photo=None,
    )


@router.callback_query(TasksState.create_due, F.data.startswith("tasks:create_due:"))
async def cb_tasks_create_due(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    parts = cb.data.split(":")
    if len(parts) != 3:
        return
    preset = parts[2]
    due_dt = _due_from_preset(preset)
    await state.update_data(draft_due_at=due_dt.isoformat() if due_dt else None)
    await state.set_state(TasksState.create_assignees)
    await _render_create_assignees(cb, state, page=0)


@router.callback_query(TasksState.create_assignees, F.data.startswith("tasks:create_assignees_page:"))
async def cb_tasks_create_assignees_page(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    parts = cb.data.split(":")
    if len(parts) != 3:
        return
    try:
        page = int(parts[2])
    except Exception:
        page = 0
    await _render_create_assignees(cb, state, page=page)


@router.callback_query(TasksState.create_assignees, F.data.startswith("tasks:create_assignee:"))
async def cb_tasks_create_toggle_assignee(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    parts = cb.data.split(":")
    if len(parts) != 3:
        return
    try:
        uid = int(parts[2])
    except Exception:
        return
    data = await state.get_data()
    ids = [int(x) for x in (data.get("draft_assignee_ids") or [])]
    if uid in ids:
        ids = [x for x in ids if x != uid]
    else:
        ids.append(uid)
    merged_map = dict(data.get("draft_assignee_map") or {})
    selected_names = [merged_map.get(int(x)) for x in sorted(ids) if merged_map.get(int(x))]
    await state.update_data(draft_assignee_ids=ids, draft_assignee_names=selected_names)
    page = int(data.get("create_assignees_page") or 0)
    await _render_create_assignees(cb, state, page=page)


@router.callback_query(TasksState.create_assignees, F.data == "tasks:create_assignees_done")
async def cb_tasks_create_assignees_done(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    await state.set_state(TasksState.create_confirm)
    await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(cb.message.chat.id),
        text=_with_draft(data, "✅ <b>Подтверждение</b>\n\nПроверьте данные и нажмите <b>Создать</b>.", full=True),
        reply_markup=tasks_create_confirm_kb(),
        state=state,
        photo=None,
    )


@router.callback_query(TasksState.create_confirm, F.data == "tasks:create_back_assignees")
async def cb_tasks_create_back_assignees(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(TasksState.create_assignees)
    data = await state.get_data()
    page = int(data.get("create_assignees_page") or 0)
    await _render_create_assignees(cb, state, page=page)


@router.callback_query(TasksState.create_confirm, F.data == "tasks:create_confirm")
async def cb_tasks_create_confirm(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    actor = await ensure_registered_or_reply(cb)
    if not actor:
        return
    data = await state.get_data()
    title = str(data.get("draft_title") or "").strip()
    desc = data.get("draft_description")
    pr = str(data.get("draft_priority") or TaskPriority.NORMAL.value)
    due_iso = data.get("draft_due_at")
    photo_fid = data.get("draft_photo_file_id")
    assignee_ids = [int(x) for x in (data.get("draft_assignee_ids") or [])]

    due_dt = None
    if due_iso:
        try:
            due_dt = datetime.fromisoformat(str(due_iso))
        except Exception:
            due_dt = None

    upload_payload = None
    if photo_fid:
        upload_payload = await _upload_tg_photo_to_web_storage(tg_file_id=str(photo_fid), bot=cb.bot)

    async with get_async_session() as session:
        repo = TaskRepository(session)
        t = await repo.create_task(
            title=title,
            description=(str(desc) if desc is not None else None),
            priority=pr,
            due_at=due_dt,
            created_by_user_id=int(actor.id),
            assignee_user_ids=assignee_ids,
            photo_file_id=(str(photo_fid) if photo_fid else None),
        )

        if photo_fid and upload_payload:
            try:
                await repo.update_task_photo_storage(
                    task_id=int(t.id),
                    photo_key=str(upload_payload.get("photo_key") or "") or None,
                    photo_url=str(upload_payload.get("photo_url") or "") or None,
                    photo_path=str(upload_payload.get("photo_path") or "") or None,
                    tg_photo_file_id=str(photo_fid),
                )
            except Exception:
                pass

    await _preserve_tasks_ui_and_clear_state(state)
    await state.update_data(tasks_chat_id=int(cb.message.chat.id))
    await _show_task_detail(cb, state, task_id=int(t.id))


@router.callback_query(F.data.startswith("tasks:list:"))
async def cb_tasks_list(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 5:
        await cb.answer("Ошибка")
        return
    _, _, scope, status, page_s = parts
    try:
        page = int(page_s)
    except Exception:
        page = 0

    await cb.answer()
    await _show_list_scope_status(cb, state, scope=str(scope), status=str(status), page=int(page))


@router.callback_query(F.data.startswith("tasks:open:"))
async def cb_tasks_open(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 6:
        await cb.answer("Ошибка")
        return
    _, _, task_id_s, scope, status, page_s = parts
    try:
        task_id = int(task_id_s)
    except Exception:
        await cb.answer("Ошибка")
        return

    await cb.answer()
    try:
        await state.update_data(
            tasks_scope=str(scope),
            tasks_status=str(status),
            tasks_page=int(page_s),
            tasks_last_list_context={"scope": str(scope), "status": str(status), "page": int(page_s)},
            tasks_list_kind=f"{scope}:{status}",
            tasks_list_page=int(page_s),
        )
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

        r = role_flags(
            tg_id=int(cb.from_user.id),
            admin_ids=settings.admin_ids,
            status=getattr(actor, "status", None),
            position=getattr(actor, "position", None),
        )
        board_url = await build_task_board_magic_link(
            session=session,
            user=actor,
            task_id=int(task.id),
            is_admin=bool(r.is_admin),
            is_manager=bool(r.is_manager),
        )

        data = await state.get_data()
        llc = dict(data.get("tasks_last_list_context") or {})
        scope = str(llc.get("scope") or data.get("tasks_scope") or "mine")
        status = str(llc.get("status") or data.get("tasks_status") or TaskStatus.NEW.value)
        page = int(llc.get("page") or data.get("tasks_page") or 0)
        html = svc.render_task_detail_html(task, perms=perms) + f"\n\n🌐 Открыть на доске: <a href=\"{esc(board_url)}\">перейти</a>"

        kb = task_detail_kb(
            task_id=int(task.id),
            can_take=bool(perms.take_in_progress),
            can_to_review=bool(perms.finish_to_review),
            can_accept_done=bool(perms.accept_done),
            can_send_back=bool(perms.send_back),
            back_cb=f"tasks:list:{scope}:{status}:{page}",
        )

        tg_fid = getattr(task, "tg_photo_file_id", None) or getattr(task, "photo_file_id", None) or None
        photo_url = getattr(task, "photo_url", None) or None
        photo_path = getattr(task, "photo_path", None) or None
        download_url = _build_web_download_url(photo_url=photo_url, photo_path=photo_path)

        # Iron-clad rendering:
        # 1) try tg file id
        # 2) else try URL
        # 3) if URL rejected by TG, download bytes and send as file
        if tg_fid:
            await render_tasks_screen(
                bot=cb.bot,
                chat_id=int(cb.message.chat.id),
                text=html,
                reply_markup=kb,
                state=state,
                photo=str(tg_fid),
            )
            return

        if photo_url:
            try:
                prev_data = await state.get_data()
                prev_msg_id = prev_data.get("tasks_root_message_id") or prev_data.get("tasks_message_id")
                sent = await cb.bot.send_photo(
                    chat_id=int(cb.message.chat.id),
                    photo=str(photo_url),
                    caption=html,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
                if prev_msg_id:
                    try:
                        await cb.bot.delete_message(chat_id=int(cb.message.chat.id), message_id=int(prev_msg_id))
                    except Exception:
                        pass
                await state.update_data(
                    tasks_root_message_id=int(sent.message_id),
                    tasks_root_chat_id=int(cb.message.chat.id),
                    tasks_root_has_media=True,
                    tasks_message_id=int(sent.message_id),
                    tasks_chat_id=int(cb.message.chat.id),
                    tasks_has_media=True,
                )
                try:
                    fid = sent.photo[-1].file_id if getattr(sent, "photo", None) else None
                except Exception:
                    fid = None
                if fid:
                    try:
                        await repo.update_task_photo_storage(
                            task_id=int(task.id),
                            photo_key=None,
                            photo_url=None,
                            photo_path=None,
                            tg_photo_file_id=str(fid),
                        )
                    except Exception:
                        pass
                return
            except Exception:
                pass

        # bytes fallback (works for web-created tasks even with 127.0.0.1 URLs)
        if download_url:
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(str(download_url))
                    if r.status_code == 200 and r.content:
                        up = BufferedInputFile(bytes(r.content), filename="task.jpg")
                        prev_data = await state.get_data()
                        prev_msg_id = prev_data.get("tasks_root_message_id") or prev_data.get("tasks_message_id")
                        sent = await cb.bot.send_photo(
                            chat_id=int(cb.message.chat.id),
                            photo=up,
                            caption=html,
                            parse_mode="HTML",
                            reply_markup=kb,
                        )
                        if prev_msg_id:
                            try:
                                await cb.bot.delete_message(chat_id=int(cb.message.chat.id), message_id=int(prev_msg_id))
                            except Exception:
                                pass
                        await state.update_data(
                            tasks_root_message_id=int(sent.message_id),
                            tasks_root_chat_id=int(cb.message.chat.id),
                            tasks_root_has_media=True,
                            tasks_message_id=int(sent.message_id),
                            tasks_chat_id=int(cb.message.chat.id),
                            tasks_has_media=True,
                        )
                        try:
                            fid = sent.photo[-1].file_id if getattr(sent, "photo", None) else None
                        except Exception:
                            fid = None
                        if fid:
                            try:
                                await repo.update_task_photo_storage(
                                    task_id=int(task.id),
                                    photo_key=None,
                                    photo_url=None,
                                    photo_path=None,
                                    tg_photo_file_id=str(fid),
                                )
                            except Exception:
                                pass
                        return
            except Exception:
                logging.exception("Failed to download/send task photo", extra={"task_id": int(getattr(task, "id", 0) or 0)})

        if (photo_url or photo_path) and not tg_fid:
            await render_tasks_screen(
                bot=cb.bot,
                chat_id=int(cb.message.chat.id),
                text=html + "\n\n⚠️ Фото недоступно.",
                reply_markup=kb,
                state=state,
                photo=None,
            )
            return

        await state.update_data(tasks_chat_id=int(cb.message.chat.id))
        await render_tasks_screen(
            bot=cb.bot,
            chat_id=int(cb.message.chat.id),
            text=html,
            reply_markup=kb,
            state=state,
            photo=None,
        )


@router.callback_query(F.data.startswith("tasks:status:"))
async def cb_tasks_select_status(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 4:
        await cb.answer("Ошибка")
        return
    await cb.answer()
    _, _, scope, status = parts
    await _show_list_scope_status(cb, state, scope=str(scope), status=str(status), page=0)


@router.callback_query(F.data.startswith("tasks:chg:"))
async def cb_tasks_change_status(cb: CallbackQuery, state: FSMContext):
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
            if code == "comment_required":
                await render_tasks_screen(
                    bot=cb.bot,
                    chat_id=int(cb.message.chat.id),
                    text="⚠️ Нужен комментарий для этого действия.",
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
    mid, _has_media = await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(chat_id),
        text="💬 <b>Комментарий</b>\n\nВведите текст комментария (обязательно, либо фото на следующем шаге):",
        reply_markup=tasks_text_cancel_kb(task_id=task_id),
        state=state,
        photo=None,
    )
    await state.update_data(active_bot_chat_id=int(chat_id), active_bot_message_id=int(mid) if mid else None)


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
    await send_new_and_delete_active(
        message=message,
        state=state,
        text="📎 Отправьте фото (можно несколько) или нажмите <b>Пропустить</b>.",
        reply_markup=kb,
        sync_to_tasks_screen=True,
    )


@router.callback_query(F.data.startswith("tasks:rework:"))
async def cb_rework(cb: CallbackQuery, state: FSMContext):
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
    mid, _has_media = await render_tasks_screen(
        bot=cb.bot,
        chat_id=int(chat_id),
        text="↩️ <b>На доработку</b>\n\nВведите комментарий (обязательно):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"tasks:rework_cancel:{int(task_id)}")]]
        ),
        state=state,
        photo=None,
    )
    await state.update_data(active_bot_chat_id=int(chat_id), active_bot_message_id=int(mid) if mid else None)


@router.callback_query(F.data.startswith("tasks:rework_cancel:"))
async def cb_rework_cancel(cb: CallbackQuery, state: FSMContext):
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


@router.message(TasksState.rework_text)
async def st_rework_text(message: Message, state: FSMContext):
    comment = (message.text or "").strip()
    if not comment:
        data0 = await state.get_data()
        task_id0 = int(data0.get("task_id") or 0)
        await send_new_and_delete_active(
            message=message,
            state=state,
            text="⚠️ Комментарий обязателен. Введите текст.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=f"tasks:rework_cancel:{int(task_id0)}")]]
            ),
            sync_to_tasks_screen=True,
        )
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
            llc = dict(data2.get("tasks_last_list_context") or {})
            scope = str(llc.get("scope") or data2.get("tasks_scope") or "mine")
            status = str(llc.get("status") or data2.get("tasks_status") or TaskStatus.NEW.value)
            page = int(llc.get("page") or data2.get("tasks_page") or 0)
            html = svc2.render_task_detail_html(task2, perms=perms2) + "\n\n✅ Отправлено на доработку."

            photo = getattr(task2, "photo_file_id", None) or None

            kb = task_detail_kb(
                task_id=int(task2.id),
                can_take=bool(perms2.take_in_progress),
                can_to_review=bool(perms2.finish_to_review),
                can_accept_done=bool(perms2.accept_done),
                can_send_back=bool(perms2.send_back),
                back_cb=f"tasks:list:{scope}:{status}:{page}",
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
