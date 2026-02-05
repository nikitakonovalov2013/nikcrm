from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import asyncpg
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from shared.config import settings
from shared.db import get_async_session
from shared.enums import TaskPriority, TaskStatus
from shared.utils import format_moscow, utc_now
from shared.permissions import role_flags

from bot.app.repository.task_notifications import TaskNotificationRepository
from bot.app.repository.tasks import TaskRepository
from bot.app.services.tasks import TasksService
from bot.app.keyboards.tasks import task_detail_kb
from bot.app.utils.html import esc, format_plain_url
from bot.app.utils.urls import build_tasks_board_magic_link


_logger = logging.getLogger(__name__)


def _asyncpg_dsn() -> str:
    dsn = str(getattr(settings, "DATABASE_URL", "") or "")
    # SQLAlchemy URL -> asyncpg URL
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://") :]
    return dsn


async def notifications_listener(*, wakeup: asyncio.Event) -> None:
    """Listen for NOTIFY from web/bot after commit and wake up worker loop.

    We still rely on DB state (pending + scheduled_at <= now), so spurious signals are safe.
    """

    dsn = _asyncpg_dsn()
    if not dsn:
        _logger.warning("task notifications listener disabled: empty DATABASE_URL")
        return

    while True:
        conn = None
        try:
            conn = await asyncpg.connect(dsn)

            def _on_notify(_conn, _pid, _channel, _payload):
                try:
                    wakeup.set()
                except Exception:
                    pass

            await conn.add_listener("task_notifications", _on_notify)
            _logger.info("task notifications listener started")

            # keep connection alive
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception("task notifications listener error; reconnecting")
            await asyncio.sleep(2)
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass


def _format_task_short(task) -> str:
    title = (getattr(task, "title", "") or "").strip()
    st = getattr(task, "status", None)
    st_val = st.value if hasattr(st, "value") else str(st or "")
    pr = getattr(task, "priority", None)
    pr_val = pr.value if hasattr(pr, "value") else str(pr or "")
    due_at = getattr(task, "due_at", None)
    due_str = format_moscow(due_at, "%d.%m.%Y %H:%M") if due_at else ""

    created_at = getattr(task, "created_at", None)
    created_str = format_moscow(created_at, "%d.%m.%Y %H:%M") if created_at else ""
    created_by = getattr(task, "created_by_user", None)
    created_by_str = "—"
    if created_by is not None:
        fio = f"{(getattr(created_by, 'first_name', '') or '').strip()} {(getattr(created_by, 'last_name', '') or '').strip()}".strip()
        created_by_str = fio or f"#{int(getattr(created_by, 'id', 0) or 0)}"

    def _elapsed_hm_local(dt) -> str:
        if not dt:
            return "—"
        try:
            now = utc_now()
            _dt = dt
            if getattr(_dt, "tzinfo", None) is None:
                _dt = _dt.replace(tzinfo=now.tzinfo)
            sec = int((now - _dt).total_seconds())
            if sec < 0:
                sec = 0
            h = sec // 3600
            m = (sec % 3600) // 60
            return f"{int(h)} ч {int(m):02d} мин"
        except Exception:
            return "—"

    def _status_human_local(v: str) -> str:
        return {
            TaskStatus.NEW.value: "Новая",
            TaskStatus.IN_PROGRESS.value: "В работе",
            TaskStatus.REVIEW.value: "На проверке",
            TaskStatus.DONE.value: "Выполнено",
            TaskStatus.ARCHIVED.value: "Архив",
        }.get(v, v)

    def _priority_human_local(v: str) -> str:
        if v == TaskPriority.URGENT.value:
            return "🔥 Срочная"
        if v == TaskPriority.FREE_TIME.value:
            return "В свободное время"
        return "Обычная"

    lines: list[str] = []
    lines.append(f"<b>{esc(title)}</b>")
    lines.append(f"<b>Статус:</b> {_status_human_local(str(st_val))}")
    lines.append(f"<b>Приоритет:</b> {_priority_human_local(str(pr_val))}")
    lines.append(f"🕒 <b>Создано:</b> {esc(created_str) if created_str else '—'}")
    lines.append(f"👤 <b>Поставил:</b> {esc(created_by_str)}")
    lines.append(f"⏱ <b>Прошло:</b> {esc(_elapsed_hm_local(created_at))}")
    if due_str:
        lines.append(f"<b>Дедлайн (МСК):</b> {esc(due_str)}")
    return "\n".join(lines)


def _status_human_local(v: str) -> str:
    return {
        TaskStatus.NEW.value: "Новая",
        TaskStatus.IN_PROGRESS.value: "В работе",
        TaskStatus.REVIEW.value: "На проверке",
        TaskStatus.DONE.value: "Выполнено",
        TaskStatus.ARCHIVED.value: "Архив",
    }.get(v, v)


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

    if typ == "created":
        return f"🆕 <b>Новая задача</b>\n\n{base}\n\n<b>Инициатор:</b> {esc(actor_name)}"
    if typ == "status_changed":
        fr = str(payload.get("from") or "")
        to = str(payload.get("to") or "")
        comment = str(payload.get("comment") or "").strip()
        if fr == TaskStatus.REVIEW.value and to == TaskStatus.IN_PROGRESS.value:
            extra = ""
            if comment:
                extra = f"\n\n<b>Комментарий:</b>\n{esc(comment)}"
            return (
                f"↩️ <b>Задача возвращена на доработку</b>\n\n{base}\n\n"
                f"<b>Кто вернул:</b> {esc(actor_name)}{extra}"
            )
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


def _format_status_changed_compact(*, task, payload: dict, board_url: str) -> str:
    title = esc((getattr(task, "title", "") or "").strip())
    fr = _status_human_local(str(payload.get("from") or ""))
    to = _status_human_local(str(payload.get("to") or ""))
    due_at = getattr(task, "due_at", None)
    due_str = format_moscow(due_at, "%d.%m.%Y %H:%M") if due_at else ""

    created_at = getattr(task, "created_at", None)
    created_str = format_moscow(created_at, "%d.%m.%Y %H:%M") if created_at else ""
    created_by = getattr(task, "created_by_user", None)
    created_by_str = "—"
    if created_by is not None:
        fio = f"{(getattr(created_by, 'first_name', '') or '').strip()} {(getattr(created_by, 'last_name', '') or '').strip()}".strip()
        created_by_str = fio or f"#{int(getattr(created_by, 'id', 0) or 0)}"

    def _elapsed_hm_local(dt) -> str:
        if not dt:
            return "—"
        try:
            now = utc_now()
            _dt = dt
            if getattr(_dt, "tzinfo", None) is None:
                _dt = _dt.replace(tzinfo=now.tzinfo)
            sec = int((now - _dt).total_seconds())
            if sec < 0:
                sec = 0
            h = sec // 3600
            m = (sec % 3600) // 60
            return f"{int(h)} ч {int(m):02d} мин"
        except Exception:
            return "—"
    assignees = list(getattr(task, "assignees", None) or [])
    assignees_str = ""
    if assignees:
        assignees_str = ", ".join((str(getattr(u, "first_name", "") or "").strip() + " " + str(getattr(u, "last_name", "") or "").strip()).strip() or str(getattr(u, "tg_id", "") or "") for u in assignees)
        assignees_str = esc(assignees_str)

    lines: list[str] = []
    lines.append(f"🔔 <b>{title}</b>")
    lines.append("")
    lines.append(f"<b>Статус:</b> {esc(fr)} → {esc(to)}")
    lines.append(f"🕒 <b>Создано:</b> {esc(created_str) if created_str else '—'}")
    lines.append(f"👤 <b>Поставил:</b> {esc(created_by_str)}")
    lines.append(f"⏱ <b>Прошло:</b> {esc(_elapsed_hm_local(created_at))}")
    if due_str:
        lines.append(f"<b>Дедлайн (МСК):</b> {esc(due_str)}")
    if assignees_str:
        lines.append(f"<b>Исполнители:</b> {assignees_str}")
    lines.append("")
    lines.append(format_plain_url("🌐 Доска задач:", str(board_url)))
    return "\n".join(lines)


async def notifications_worker(*, bot, poll_seconds: int = 20, batch_size: int = 30) -> None:
    _logger.info("task notifications worker started", extra={"poll_seconds": poll_seconds})

    wakeup = asyncio.Event()
    listener_task: asyncio.Task | None = None
    try:
        try:
            listener_task = asyncio.create_task(notifications_listener(wakeup=wakeup))
        except Exception:
            _logger.exception("failed to start task notifications listener")

        while True:
            try:
                # Coalesce bursts of signals.
                wakeup.clear()

                now = utc_now()
                async with get_async_session() as session:
                    repo = TaskNotificationRepository(session)
                    items = await repo.fetch_due_pending(now=now, limit=batch_size)

                    for n in items:
                        await repo.inc_attempts(n=n)
                        try:
                            recipient = getattr(n, "recipient_user", None)
                            chat_id = int(getattr(recipient, "tg_id", 0) or 0)
                            if chat_id <= 0:
                                try:
                                    _logger.info(
                                        "TASK_NOTIFY_SKIP reason=no_tg_id source=worker notification_id=%s type=%s task_id=%s recipient_user_id=%s",
                                        int(getattr(n, "id", 0) or 0),
                                        str(getattr(n, "type", "")),
                                        int(getattr(n, "task_id", 0) or 0),
                                        int(getattr(recipient, "id", 0) or 0),
                                    )
                                except Exception:
                                    pass
                                await repo.mark_failed(n=n, now=now, error="no tg_id for recipient", retry_at=None)
                                continue
                            task = getattr(n, "task", None)
                            task_id = int(getattr(task, "id")) if task is not None else int(getattr(n, "task_id"))

                            try:
                                _logger.info(
                                    "TASK_NOTIFY_PROCESS source=worker notification_id=%s type=%s task_id=%s recipient_user_id=%s chat_id=%s",
                                    int(getattr(n, "id", 0) or 0),
                                    str(getattr(n, "type", "")),
                                    int(task_id),
                                    int(getattr(recipient, "id", 0) or 0),
                                    int(chat_id),
                                )
                            except Exception:
                                pass

                            # Build the same keyboard as in task detail view, using existing permissions logic.
                            tasks_repo = TaskRepository(session)
                            tasks_svc = TasksService(tasks_repo)
                            actor, task2, perms = await tasks_svc.get_detail(tg_id=chat_id, task_id=int(task_id))
                            if not actor or not task2 or not perms:
                                # Fallback to legacy text-only, but without "Открыть задачу" button.
                                text = render_notification_html(n=n)
                                sent = await bot.send_message(
                                    chat_id=chat_id,
                                    text=text,
                                    parse_mode="HTML",
                                    reply_markup=None,
                                    disable_web_page_preview=True,
                                )
                                await repo.store_delivery_info(n=n, chat_id=int(chat_id), message_id=int(sent.message_id))
                                await repo.mark_sent(n=n, now=now)
                                try:
                                    _logger.info(
                                        "TASK_NOTIFY_SENT source=worker notification_id=%s type=%s task_id=%s chat_id=%s message_id=%s mode=send_fallback",
                                        int(getattr(n, "id", 0) or 0),
                                        str(getattr(n, "type", "")),
                                        int(task_id),
                                        int(chat_id),
                                        int(sent.message_id),
                                    )
                                except Exception:
                                    pass
                                continue

                            r = role_flags(
                                tg_id=int(chat_id),
                                admin_ids=settings.admin_ids,
                                status=getattr(actor, "status", None),
                                position=getattr(actor, "position", None),
                            )
                            board_url = await build_tasks_board_magic_link(
                                session=session,
                                user=actor,
                                is_admin=bool(r.is_admin),
                                is_manager=bool(r.is_manager),
                                ttl_minutes=60,
                            )

                            can_edit = bool(r.is_admin or r.is_manager)
                            is_archived = str(task2.status.value if hasattr(task2.status, "value") else str(task2.status)) == TaskStatus.ARCHIVED.value
                            kb = task_detail_kb(
                                task_id=int(task2.id),
                                can_take=bool(perms.take_in_progress),
                                can_to_review=bool(perms.finish_to_review),
                                can_accept_done=bool(perms.accept_done),
                                can_send_back=bool(perms.send_back),
                                can_edit=bool(can_edit),
                                can_archive=bool(perms.archive),
                                can_unarchive=bool(perms.unarchive),
                                is_archived=bool(is_archived),
                                back_cb="tasks:menu",
                            )

                            payload = dict(getattr(n, "payload", None) or {})
                            n_type = str(getattr(n, "type", ""))
                            if n_type == "status_changed":
                                # Special-case 'return to rework' to guarantee comment is visible.
                                action = str(payload.get("action") or "")
                                fr = str(payload.get("from") or "")
                                to = str(payload.get("to") or "")
                                is_rework = action == "return_to_rework" or (
                                    fr == TaskStatus.REVIEW.value and to == TaskStatus.IN_PROGRESS.value and str(payload.get("comment") or "").strip()
                                )
                                if is_rework:
                                    text = render_notification_html(n=n) + "\n\n" + format_plain_url("🔗 Открыть задачу:", str(board_url))
                                else:
                                    text = _format_status_changed_compact(task=task2, payload=payload, board_url=board_url)
                            else:
                                # Keep existing formats for other notification types, but append board URL explicitly.
                                text = render_notification_html(n=n) + "\n\n" + format_plain_url("🌐 Доска задач:", str(board_url))

                            # Prefer edit of last sent notification for this task+recipient.
                            # BUT: for some types we must send a new message to ensure the user gets a visible notification.
                            typ = str(getattr(n, "type", ""))
                            force_new = False
                            if typ == "created":
                                force_new = True
                            if typ == "status_changed":
                                try:
                                    action = str(payload.get("action") or "")
                                    fr = str(payload.get("from") or "")
                                    to = str(payload.get("to") or "")
                                    if action == "return_to_rework":
                                        force_new = True
                                    elif fr == TaskStatus.REVIEW.value and to == TaskStatus.IN_PROGRESS.value:
                                        force_new = True
                                except Exception:
                                    pass

                            edited = False
                            if not force_new:
                                last_sent = await repo.get_last_sent_for_task_recipient(task_id=int(task_id), recipient_user_id=int(getattr(recipient, "id")))
                                if last_sent is not None:
                                    lp = dict(getattr(last_sent, "payload", None) or {})
                                    last_chat_id = lp.get("tg_chat_id")
                                    last_message_id = lp.get("tg_message_id")
                                    if last_chat_id and last_message_id:
                                        try:
                                            await bot.edit_message_text(
                                                chat_id=int(last_chat_id),
                                                message_id=int(last_message_id),
                                                text=text,
                                                parse_mode="HTML",
                                                reply_markup=kb,
                                                disable_web_page_preview=True,
                                            )
                                            await repo.store_delivery_info(n=n, chat_id=int(last_chat_id), message_id=int(last_message_id))
                                            edited = True
                                        except Exception:
                                            edited = False

                            if not edited:
                                sent = await bot.send_message(
                                    chat_id=chat_id,
                                    text=text,
                                    parse_mode="HTML",
                                    reply_markup=kb,
                                    disable_web_page_preview=True,
                                )
                                await repo.store_delivery_info(n=n, chat_id=int(chat_id), message_id=int(sent.message_id))
                                try:
                                    _logger.info(
                                        "TASK_NOTIFY_SENT source=worker notification_id=%s type=%s task_id=%s chat_id=%s message_id=%s mode=send",
                                        int(getattr(n, "id", 0) or 0),
                                        str(getattr(n, "type", "")),
                                        int(task_id),
                                        int(chat_id),
                                        int(sent.message_id),
                                    )
                                except Exception:
                                    pass
                            else:
                                try:
                                    _logger.info(
                                        "TASK_NOTIFY_SENT source=worker notification_id=%s type=%s task_id=%s chat_id=%s message_id=%s mode=edit",
                                        int(getattr(n, "id", 0) or 0),
                                        str(getattr(n, "type", "")),
                                        int(task_id),
                                        int(chat_id),
                                        int(getattr(n, "tg_message_id", 0) or 0),
                                    )
                                except Exception:
                                    pass

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

            # Event-driven wakeup + fallback polling.
            try:
                await asyncio.wait_for(wakeup.wait(), timeout=int(poll_seconds))
            except asyncio.TimeoutError:
                pass
    finally:
        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except Exception:
                pass
