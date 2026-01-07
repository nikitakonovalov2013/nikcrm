from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from shared.db import get_async_session
from shared.enums import TaskPriority, TaskStatus
from shared.utils import format_moscow, utc_now

from bot.app.repository.task_notifications import TaskNotificationRepository
from bot.app.utils.html import esc


_logger = logging.getLogger(__name__)


def _open_task_kb(*, task_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть задачу", callback_data=f"tasks:open_notify:{int(task_id)}")]]
    )


def _format_task_short(task) -> str:
    title = (getattr(task, "title", "") or "").strip()
    st = getattr(task, "status", None)
    st_val = st.value if hasattr(st, "value") else str(st or "")
    pr = getattr(task, "priority", None)
    pr_val = pr.value if hasattr(pr, "value") else str(pr or "")
    due_at = getattr(task, "due_at", None)
    due_str = format_moscow(due_at, "%d.%m.%Y %H:%M") if due_at else ""

    def _status_human_local(v: str) -> str:
        return {
            TaskStatus.NEW.value: "Новая",
            TaskStatus.IN_PROGRESS.value: "В работе",
            TaskStatus.REVIEW.value: "На проверке",
            TaskStatus.DONE.value: "Выполнено",
            TaskStatus.ARCHIVED.value: "Архив",
        }.get(v, v)

    def _priority_human_local(v: str) -> str:
        return "🔥 Срочная" if v == TaskPriority.URGENT.value else "Обычная"

    lines: list[str] = []
    lines.append(f"<b>{esc(title)}</b>")
    lines.append(f"<b>Статус:</b> {_status_human_local(str(st_val))}")
    lines.append(f"<b>Приоритет:</b> {_priority_human_local(str(pr_val))}")
    if due_str:
        lines.append(f"<b>Дедлайн (МСК):</b> {esc(due_str)}")
    return "\n".join(lines)


def render_notification_html(*, n) -> str:
    task = getattr(n, "task", None)
    payload = dict(getattr(n, "payload", None) or {})
    typ = str(getattr(n, "type", ""))

    actor_name = "—"
    try:
        actor_name = str(payload.get("actor_name") or "—")
    except Exception:
        actor_name = "—"

    base = _format_task_short(task) if task else f"<b>Задача #{payload.get('task_id')}</b>"

    def _status_human_local(v: str) -> str:
        return {
            TaskStatus.NEW.value: "Новая",
            TaskStatus.IN_PROGRESS.value: "В работе",
            TaskStatus.REVIEW.value: "На проверке",
            TaskStatus.DONE.value: "Выполнено",
            TaskStatus.ARCHIVED.value: "Архив",
        }.get(v, v)

    if typ == "created":
        return f"🆕 <b>Новая задача</b>\n\n{base}\n\n<b>Инициатор:</b> {esc(actor_name)}"
    if typ == "status_changed":
        fr = str(payload.get("from") or "")
        to = str(payload.get("to") or "")
        comment = str(payload.get("comment") or "").strip()
        extra = ""
        if comment:
            extra = f"\n\n<b>Комментарий:</b>\n{esc(comment)}"
        return (
            f"🔔 <b>Смена статуса</b>\n\n{base}\n\n"
            f"<b>Было:</b> {_status_human_local(fr)}\n<b>Стало:</b> {_status_human_local(to)}\n\n<b>Инициатор:</b> {esc(actor_name)}{extra}"
        )
    if typ == "comment":
        text = str(payload.get("text") or "").strip()
        snippet = text
        if len(snippet) > 700:
            snippet = snippet[:700] + "…"
        extra = f"\n\n<b>Текст:</b>\n{esc(snippet)}" if snippet else ""
        return f"💬 <b>Новый комментарий</b>\n\n{base}\n\n<b>Автор:</b> {esc(actor_name)}{extra}"
    if typ == "remind":
        return f"🔔 <b>Напоминание</b>\n\n{base}\n\n<b>Инициатор:</b> {esc(actor_name)}"

    return f"🔔 <b>Уведомление</b>\n\n{base}"


async def notifications_worker(*, bot, poll_seconds: int = 20, batch_size: int = 30) -> None:
    _logger.info("task notifications worker started", extra={"poll_seconds": poll_seconds})
    while True:
        try:
            now = utc_now()
            async with get_async_session() as session:
                repo = TaskNotificationRepository(session)
                items = await repo.fetch_due_pending(now=now, limit=batch_size)

                if not items:
                    # commit happens in get_async_session
                    pass

                for n in items:
                    await repo.inc_attempts(n=n)
                    try:
                        recipient = getattr(n, "recipient_user", None)
                        chat_id = int(getattr(recipient, "tg_id"))
                        task = getattr(n, "task", None)
                        task_id = int(getattr(task, "id")) if task is not None else int(getattr(n, "task_id"))
                        text = render_notification_html(n=n)

                        await bot.send_message(chat_id=chat_id, text=text, reply_markup=_open_task_kb(task_id=task_id))
                        await repo.mark_sent(n=n, now=now)
                    except Exception as e:
                        # basic 3 attempts with simple backoff
                        attempts = int(getattr(n, "attempts", 0) or 0)
                        err = repr(e)
                        retry_at = None
                        if attempts < 3:
                            retry_at = now + timedelta(minutes=2 * attempts)
                        await repo.mark_failed(n=n, now=now, error=err, retry_at=retry_at)

        except asyncio.CancelledError:
            _logger.info("task notifications worker cancelled")
            raise
        except Exception:
            _logger.exception("task notifications worker loop error")

        await asyncio.sleep(int(poll_seconds))
