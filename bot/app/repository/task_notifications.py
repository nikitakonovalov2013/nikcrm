from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.models import TaskNotification, Task, User


class TaskNotificationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_last_sent_for_task_recipient(
        self, *, task_id: int, recipient_user_id: int
    ) -> TaskNotification | None:
        q = (
            select(TaskNotification)
            .where(TaskNotification.task_id == int(task_id))
            .where(TaskNotification.recipient_user_id == int(recipient_user_id))
            .where(TaskNotification.status == "sent")
            .order_by(TaskNotification.sent_at.desc().nullslast(), TaskNotification.id.desc())
            .limit(1)
        )
        res = await self.session.execute(q)
        return res.scalars().first()

    async def fetch_due_pending(self, *, now: datetime, limit: int = 50) -> list[TaskNotification]:
        q = (
            select(TaskNotification)
            .where(TaskNotification.status == "pending")
            .where(TaskNotification.scheduled_at <= now)
            .where(or_(TaskNotification.next_retry_at.is_(None), TaskNotification.next_retry_at <= now))
            .order_by(TaskNotification.scheduled_at.asc(), TaskNotification.id.asc())
            .options(
                selectinload(TaskNotification.task)
                .selectinload(Task.assignees),
                selectinload(TaskNotification.task)
                .selectinload(Task.created_by_user),
                selectinload(TaskNotification.task)
                .selectinload(Task.started_by_user),
                selectinload(TaskNotification.task)
                .selectinload(Task.completed_by_user),
                selectinload(TaskNotification.recipient_user),
            )
            .with_for_update(skip_locked=True)
            .limit(int(limit))
        )
        res = await self.session.execute(q)
        return list(res.scalars().unique().all())

    async def mark_sent(self, *, n: TaskNotification, now: datetime) -> None:
        n.status = "sent"
        n.sent_at = now
        n.error = None
        n.next_retry_at = None

    async def store_delivery_info(self, *, n: TaskNotification, chat_id: int, message_id: int) -> None:
        payload = dict(getattr(n, "payload", None) or {})
        payload["tg_chat_id"] = int(chat_id)
        payload["tg_message_id"] = int(message_id)
        n.payload = payload

    async def mark_failed(self, *, n: TaskNotification, now: datetime, error: str, retry_at: datetime | None) -> None:
        n.status = "failed" if retry_at is None else "pending"
        n.error = (error or "").strip()[:2000] or None
        n.next_retry_at = retry_at
        if retry_at is None:
            n.sent_at = None

    async def inc_attempts(self, *, n: TaskNotification) -> None:
        n.attempts = int(getattr(n, "attempts", 0) or 0) + 1
