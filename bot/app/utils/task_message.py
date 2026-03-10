from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from shared.enums import TaskPriority, TaskStatus
from bot.app.utils.html import esc


def _user_full_name(user) -> str:
    if user is None:
        return "—"
    first = str(getattr(user, "first_name", "") or "").strip()
    last = str(getattr(user, "last_name", "") or "").strip()
    fio = f"{first} {last}".strip()
    if fio:
        return fio
    uid = int(getattr(user, "id", 0) or 0)
    return f"#{uid}" if uid > 0 else "—"


def _priority_human_ru(priority_val: str) -> str:
    if str(priority_val) == TaskPriority.URGENT.value:
        return "Срочно"
    if str(priority_val) == TaskPriority.FREE_TIME.value:
        return "В свободное время"
    return "Обычная"


def _status_icon(task) -> str:
    st = getattr(task, "status", None)
    st_val = st.value if hasattr(st, "value") else str(st or "")
    if st_val == TaskStatus.NEW.value:
        return "🆕"
    if st_val == TaskStatus.IN_PROGRESS.value:
        return "▶️"
    if st_val == TaskStatus.DONE.value:
        return "✅"
    if st_val == TaskStatus.REVIEW.value:
        return "🟡"
    return "📌"


def render_task_message(
    task,
    context,
    viewer_user,
    actor_user=None,
    *,
    board_url: str | None = None,
    can_take: bool = False,
    menu_kb: InlineKeyboardMarkup | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    del viewer_user

    title = esc(str(getattr(task, "title", "") or "").strip() or "—")
    description = esc(str(getattr(task, "description", "") or "").strip() or "—")

    creator = getattr(task, "created_by_user", None)
    creator_name = esc(_user_full_name(creator))
    actor_name = esc(_user_full_name(actor_user))

    pr = getattr(task, "priority", None)
    pr_val = pr.value if hasattr(pr, "value") else str(pr or "")
    priority_ru = esc(_priority_human_ru(str(pr_val)))

    ctx = str(context or "")
    if ctx == "task_new_notification":
        text = (
            f"🆕 <b>НОВАЯ ЗАДАЧА: {title}</b>\n\n"
            f"{description}\n\n"
            f"👤 Поставил: {creator_name}\n"
            f"Приоритет: {priority_ru}"
        )
    elif ctx == "task_in_progress_notification":
        text = (
            f"▶️ <b>ЗАДАЧА ВЗЯТА В РАБОТУ: {title}</b>\n\n"
            f"{description}\n\n"
            f"👤 Поставил: {creator_name}\n"
            f"🙋 Взял: {actor_name}\n"
            f"Приоритет: {priority_ru}"
        )
    elif ctx == "task_done_notification":
        text = (
            f"✅ <b>ЗАДАЧА ВЫПОЛНЕНА: {title}</b>\n\n"
            f"{description}\n\n"
            f"👤 Поставил: {creator_name}\n"
            f"🙋 Выполнил: {actor_name}\n"
            f"Приоритет: {priority_ru}"
        )
    elif ctx == "task_review_notification":
        text = (
            f"🟡 <b>ЗАДАЧА НА ПРОВЕРКУ: {title}</b>\n\n"
            f"{description}\n\n"
            f"👤 Поставил: {creator_name}\n"
            f"🙋 Отправил: {actor_name}\n"
            f"Приоритет: {priority_ru}"
        )
    elif ctx == "task_rework_notification":
        text = (
            f"↩️ <b>ЗАДАЧА НА ДОРАБОТКУ: {title}</b>\n\n"
            f"{description}\n\n"
            f"👤 Поставил: {creator_name}\n"
            f"🙋 Вернул: {actor_name}\n"
            f"Приоритет: {priority_ru}"
        )
    elif ctx in {"task_menu_view", "task_after_comment"}:
        icon = _status_icon(task)
        text = (
            f"{icon} <b>ЗАДАЧА: {title}</b>\n\n"
            f"{description}\n\n"
            f"👤 Поставил: {creator_name}\n"
            f"Приоритет: {priority_ru}"
        )
    else:
        text = f"📌 <b>ЗАДАЧА: {title}</b>"

    if ctx in {
        "task_new_notification",
        "task_in_progress_notification",
        "task_done_notification",
        "task_review_notification",
        "task_rework_notification",
    }:
        task_id = int(getattr(task, "id", 0) or 0)
        rows: list[list[InlineKeyboardButton]] = []
        if ctx == "task_new_notification" and bool(can_take) and task_id > 0:
            rows.append([InlineKeyboardButton(text="▶️ Взять в работу", callback_data=f"tasks:chg:{task_id}:in_progress")])

        bottom: list[InlineKeyboardButton] = []
        if str(board_url or "").strip():
            bottom.append(InlineKeyboardButton(text="📋 Мои задачи", url=str(board_url)))
        if task_id > 0:
            bottom.append(InlineKeyboardButton(text="💬 Оставить комментарий", callback_data=f"tasks:comment:{task_id}"))
        if bottom:
            rows.append(bottom[:2])
        return text, InlineKeyboardMarkup(inline_keyboard=rows)

    return text, menu_kb
