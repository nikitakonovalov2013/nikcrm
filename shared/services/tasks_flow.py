from __future__ import annotations

import logging
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.config import settings
from shared.enums import TaskEventType, TaskStatus
from shared.models import Task, TaskComment, TaskCommentPhoto, TaskEvent
from shared.services.task_notifications import TaskNotificationService


logger = logging.getLogger(__name__)


async def load_task_full_for_actions(*, session: AsyncSession, task_id: int) -> Task | None:
    res = await session.execute(
        select(Task)
        .where(Task.id == int(task_id))
        .options(
            selectinload(Task.assignees),
            selectinload(Task.created_by_user),
            selectinload(Task.started_by_user),
        )
    )
    return res.scalar_one_or_none()


def _executor_user_ids(task: Task) -> list[int]:
    assignees = list(getattr(task, "assignees", None) or [])
    if assignees:
        return [int(getattr(u, "id", 0) or 0) for u in assignees if int(getattr(u, "id", 0) or 0) > 0]
    sb = getattr(task, "started_by_user_id", None)
    if sb is not None and int(sb) > 0:
        return [int(sb)]
    return []


def _notification_recipients_for_task(task: Task) -> tuple[list[int], dict]:
    executor_ids = _executor_user_ids(task)
    started_by_id = int(getattr(task, "started_by_user_id", 0) or 0) or 0
    created_by_id = int(getattr(task, "created_by_user_id", 0) or 0) or 0

    s: set[int] = set(int(x) for x in (executor_ids or []) if int(x) > 0)
    if started_by_id > 0:
        s.add(int(started_by_id))
    if created_by_id > 0:
        s.add(int(created_by_id))

    recipients = sorted(s)
    meta = {
        "executor_ids": [int(x) for x in (executor_ids or []) if int(x) > 0],
        "started_by_id": int(started_by_id) if started_by_id > 0 else None,
        "created_by_id": int(created_by_id) if created_by_id > 0 else None,
        "recipients": [int(x) for x in recipients],
    }
    return recipients, meta


async def _tg_send_message_http(*, chat_id: int, text: str) -> bool:
    token = str(getattr(settings, "BOT_TOKEN", "") or "").strip()
    if not token:
        raise RuntimeError("empty BOT_TOKEN")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": int(chat_id),
        "text": str(text),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, data=payload)
        r.raise_for_status()
        data = r.json()
        return bool(data.get("ok"))


async def return_task_to_rework(
    *,
    session: AsyncSession,
    task_id: int,
    actor_user_id: int,
    actor_name: str | None,
    comment: str,
    hard_send_tg: bool = False,
) -> Task:
    comment_str = (comment or "").strip()
    try:
        logger.info(
            "TASK_REWORK_REQUEST task_id=%s actor_user_id=%s comment_len=%s",
            int(task_id),
            int(actor_user_id),
            int(len(comment_str)),
        )
    except Exception:
        pass

    t = await load_task_full_for_actions(session=session, task_id=int(task_id))
    if not t:
        raise ValueError("task_not_found")

    old_status = t.status.value if hasattr(t.status, "value") else str(t.status)

    # Persist rework comment as a regular task comment (same as current behavior).
    c = TaskComment(task_id=int(t.id), author_user_id=int(actor_user_id), text=comment_str or None)
    session.add(c)

    # Status transition: review -> in_progress
    t.status = TaskStatus.IN_PROGRESS

    new_status_val = t.status.value if hasattr(t.status, "value") else str(t.status)

    ev = TaskEvent(
        task_id=int(t.id),
        actor_user_id=int(actor_user_id),
        type=TaskEventType.STATUS_CHANGED,
        payload={"from": str(old_status), "to": str(new_status_val), "comment": comment_str or None},
    )
    session.add(ev)

    await session.flush()

    recipients, meta = _notification_recipients_for_task(t)
    try:
        logger.info(
            "TASK_REWORK_SAVED task_id=%s old=%s new=%s executor_ids=%s started_by_id=%s created_by_id=%s recipients=%s actor_user_id=%s comment_id=%s",
            int(t.id),
            str(old_status),
            str(new_status_val),
            ",".join([str(int(x)) for x in meta.get("executor_ids") or []]) if meta.get("executor_ids") else "",
            str(meta.get("started_by_id") or ""),
            str(meta.get("created_by_id") or ""),
            ",".join([str(int(x)) for x in (meta.get("recipients") or [])]) if meta.get("recipients") else "",
            int(actor_user_id),
            int(getattr(c, "id", 0) or 0),
        )
    except Exception:
        pass

    if not recipients:
        return t

    ns = TaskNotificationService(session)
    tg_map = await ns.resolve_recipients_tg_ids(user_ids=list(recipients))

    for rid in recipients:
        if int(tg_map.get(int(rid), 0) or 0) <= 0:
            try:
                logger.info(
                    "TASK_REWORK_NOTIFY_SKIP reason=no_tg_id task_id=%s assignee_id=%s",
                    int(t.id),
                    int(rid),
                )
            except Exception:
                pass

    for rid in recipients:
        tg_id = int(tg_map.get(int(rid), 0) or 0)
        if tg_id <= 0:
            continue
        try:
            logger.info(
                "TASK_REWORK_NOTIFY_ATTEMPT task_id=%s to_assignee_id=%s tg_id=%s",
                int(t.id),
                int(rid),
                int(tg_id),
            )
        except Exception:
            pass

        enq = await ns.enqueue(
            task_id=int(t.id),
            recipient_user_id=int(rid),
            type="status_changed",
            payload={
                "task_id": int(t.id),
                "from": str(old_status),
                "to": str(new_status_val),
                "comment": comment_str or None,
                "action": "return_to_rework",
                "actor_user_id": int(actor_user_id),
                "actor_name": (str(actor_name) if actor_name else None),
                "event_id": int(getattr(ev, "id", 0) or 0),
            },
            dedupe_key=f"status:{int(getattr(ev, 'id', 0) or 0)}",
        )

        if hard_send_tg and bool(enq.created):
            text = (
                f"↩️ <b>Задача возвращена на доработку</b>\n\n"
                f"<b>{str(getattr(t, 'title', '') or '').strip()}</b>\n\n"
                + (f"💬 <b>Комментарий:</b>\n{comment_str}\n\n" if comment_str else "")
                + f"👤 <b>Кто вернул:</b> {str(actor_name or '').strip() or ('#' + str(int(actor_user_id)))}"
            )
            try:
                ok = await _tg_send_message_http(chat_id=int(tg_id), text=str(text))
                logger.info(
                    "TASK_REWORK_NOTIFY_SENT task_id=%s recipient_user_id=%s tg_id=%s ok=%s",
                    int(t.id),
                    int(rid),
                    int(tg_id),
                    bool(ok),
                )
            except Exception:
                logger.exception(
                    "TASK_REWORK_NOTIFY_FAILED task_id=%s recipient_user_id=%s tg_id=%s",
                    int(t.id),
                    int(rid),
                    int(tg_id),
                )

    return t


async def add_task_comment(
    *,
    session: AsyncSession,
    task_id: int,
    author_user_id: int,
    author_name: str | None,
    text: str | None,
    photo_file_ids: list[str] | None = None,
    notify: bool = True,
    notify_self: bool = True,
    hard_send_tg: bool = False,
) -> TaskComment:
    text_str = (text or "").strip()
    photos = [str(x) for x in (photo_file_ids or []) if str(x).strip()]

    try:
        logger.info(
            "TASK_COMMENT_REQUEST task_id=%s author_user_id=%s text_len=%s photos_count=%s notify=%s",
            int(task_id),
            int(author_user_id),
            int(len(text_str)),
            int(len(photos)),
            bool(notify),
        )
    except Exception:
        pass

    t = await load_task_full_for_actions(session=session, task_id=int(task_id))
    if not t:
        raise ValueError("task_not_found")

    c = TaskComment(task_id=int(t.id), author_user_id=int(author_user_id), text=(text_str or None))
    session.add(c)
    await session.flush()

    for fid in photos:
        session.add(TaskCommentPhoto(comment_id=int(c.id), tg_file_id=str(fid)))

    session.add(
        TaskEvent(
            task_id=int(t.id),
            actor_user_id=int(author_user_id),
            type=TaskEventType.COMMENT_ADDED,
            payload={"has_text": bool(text_str), "photos_count": int(len(photos))},
        )
    )

    await session.flush()

    try:
        logger.info(
            "TASK_COMMENT_SAVED task_id=%s comment_id=%s",
            int(t.id),
            int(getattr(c, "id", 0) or 0),
        )
    except Exception:
        pass

    if not notify:
        return c

    recipients, meta = _notification_recipients_for_task(t)

    if not bool(notify_self):
        recipients = [rid for rid in recipients if int(rid) != int(author_user_id)]
        meta["recipients"] = [int(x) for x in recipients]

    if not recipients:
        return c

    ns = TaskNotificationService(session)
    tg_map = await ns.resolve_recipients_tg_ids(user_ids=list(recipients))

    actor_name = (str(author_name).strip() if author_name else None) or f"#{int(author_user_id)}"

    try:
        logger.info(
            "TASK_COMMENT_RECIPIENTS task_id=%s comment_id=%s executor_ids=%s started_by_id=%s created_by_id=%s recipients=%s author_user_id=%s notify_self=%s",
            int(t.id),
            int(getattr(c, "id", 0) or 0),
            ",".join([str(int(x)) for x in meta.get("executor_ids") or []]) if meta.get("executor_ids") else "",
            str(meta.get("started_by_id") or ""),
            str(meta.get("created_by_id") or ""),
            ",".join([str(int(x)) for x in (meta.get("recipients") or [])]) if meta.get("recipients") else "",
            int(author_user_id),
            bool(notify_self),
        )
    except Exception:
        pass

    for rid in recipients:
        tg_id = int(tg_map.get(int(rid), 0) or 0)
        if tg_id <= 0:
            try:
                logger.info(
                    "TASK_COMMENT_NOTIFY_SKIP reason=no_tg_id task_id=%s recipient_user_id=%s",
                    int(t.id),
                    int(rid),
                )
            except Exception:
                pass
            continue

        try:
            logger.info(
                "TASK_COMMENT_NOTIFY_ATTEMPT task_id=%s recipient_user_id=%s tg_id=%s comment_id=%s",
                int(t.id),
                int(rid),
                int(tg_id),
                int(getattr(c, "id", 0) or 0),
            )
        except Exception:
            pass

        await ns.enqueue(
            task_id=int(t.id),
            recipient_user_id=int(rid),
            type="comment",
            payload={
                "task_id": int(t.id),
                "comment_id": int(getattr(c, "id", 0) or 0),
                "text": (text_str or ""),
                "photos_count": int(len(photos)),
                "actor_user_id": int(author_user_id),
                "actor_name": actor_name,
            },
            dedupe_key=f"comment:{int(getattr(c, 'id', 0) or 0)}",
        )

        if hard_send_tg:
            snippet = text_str
            if len(snippet) > 700:
                snippet = snippet[:700] + "…"
            msg = (
                "💬 <b>Новый комментарий к задаче</b>\n\n"
                f"<b>{str(getattr(t, 'title', '') or '').strip()}</b>\n\n"
                f"👤 <b>{actor_name}:</b> {snippet}"
            )
            try:
                ok = await _tg_send_message_http(chat_id=int(tg_id), text=str(msg))
                logger.info(
                    "TASK_COMMENT_NOTIFY_SENT task_id=%s recipient_user_id=%s tg_id=%s comment_id=%s ok=%s",
                    int(t.id),
                    int(rid),
                    int(tg_id),
                    int(getattr(c, "id", 0) or 0),
                    bool(ok),
                )
            except Exception:
                logger.exception(
                    "TASK_COMMENT_NOTIFY_FAILED task_id=%s recipient_user_id=%s tg_id=%s comment_id=%s",
                    int(t.id),
                    int(rid),
                    int(tg_id),
                    int(getattr(c, "id", 0) or 0),
                )

    return c
