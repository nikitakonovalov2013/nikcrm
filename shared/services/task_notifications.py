from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.db import add_after_commit_callback
from shared.models import Task, TaskNotification, User
from shared.utils import MOSCOW_TZ, utc_now


WORK_START = time(8, 0)
WORK_END = time(22, 0)


def next_allowed_send_at(*, now_utc: datetime) -> datetime:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    now_msk = now_utc.astimezone(MOSCOW_TZ)
    start_msk = now_msk.replace(hour=WORK_START.hour, minute=WORK_START.minute, second=0, microsecond=0)
    end_msk = now_msk.replace(hour=WORK_END.hour, minute=WORK_END.minute, second=0, microsecond=0)

    if start_msk <= now_msk < end_msk:
        return now_utc
    if now_msk < start_msk:
        return start_msk.astimezone(timezone.utc)
    # now_msk >= end
    next_day_start = (start_msk + timedelta(days=1)).replace(hour=WORK_START.hour, minute=WORK_START.minute)
    return next_day_start.astimezone(timezone.utc)


def _hash_dedupe_key(raw: str) -> str:
    # keep key short and index-friendly
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:40]


@dataclass(frozen=True)
class EnqueueResult:
    created: bool
    notification_id: int | None


class TaskNotificationService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _notify_after_commit(self, *, recipient_user_id: int, notification_id: int | None) -> None:
        # Lightweight wake-up signal for bot worker. Actual sending is still driven by DB state.
        payload = f"recipient={int(recipient_user_id)};id={int(notification_id or 0)}"
        try:
            await self.session.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": "task_notifications", "payload": payload},
            )
        except Exception:
            # Never fail core flows due to notification signal problems.
            pass

    async def enqueue(
        self,
        *,
        task_id: int,
        recipient_user_id: int,
        type: str,
        payload: dict,
        dedupe_key: str | None,
        now_utc: datetime | None = None,
    ) -> EnqueueResult:
        now_utc = now_utc or utc_now()
        scheduled_at = next_allowed_send_at(now_utc=now_utc)

        # Apply hashing to keep unique constraint stable even for long/raw keys
        dedupe_key_h = _hash_dedupe_key(dedupe_key) if dedupe_key else None

        if dedupe_key_h:
            res = await self.session.execute(
                select(TaskNotification.id).where(
                    TaskNotification.recipient_user_id == int(recipient_user_id),
                    TaskNotification.dedupe_key == str(dedupe_key_h),
                )
            )
            existing_id = res.scalar_one_or_none()
            if existing_id:
                return EnqueueResult(created=False, notification_id=int(existing_id))

        n = TaskNotification(
            task_id=int(task_id),
            recipient_user_id=int(recipient_user_id),
            type=str(type),
            payload=dict(payload or {}),
            status="pending",
            attempts=0,
            scheduled_at=scheduled_at,
            next_retry_at=None,
            sent_at=None,
            error=None,
            dedupe_key=str(dedupe_key_h) if dedupe_key_h else None,
        )
        self.session.add(n)
        await self.session.flush()

        # Event-driven wake up (after commit).
        add_after_commit_callback(
            self.session,
            lambda: self._notify_after_commit(recipient_user_id=int(recipient_user_id), notification_id=int(n.id)),
        )
        return EnqueueResult(created=True, notification_id=int(n.id))

    async def resolve_recipients_tg_ids(self, *, user_ids: list[int]) -> dict[int, int]:
        if not user_ids:
            return {}
        res = await self.session.execute(select(User.id, User.tg_id).where(User.id.in_([int(x) for x in user_ids])))
        return {int(r[0]): int(r[1]) for r in res.all()}

    async def load_task_for_message(self, *, task_id: int) -> Task | None:
        res = await self.session.execute(select(Task).where(Task.id == int(task_id)))
        return res.scalar_one_or_none()
