from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, or_

from shared.config import settings
from shared.db import get_async_session
from shared.models import TelegramOutbox


_logger = logging.getLogger(__name__)


def _now() -> datetime:
    # Use naive utc to match utc_now usage; DB column is timezone-aware anyway.
    try:
        from shared.utils import utc_now

        return utc_now()
    except Exception:
        return datetime.utcnow()


def _next_delay(attempts: int) -> int:
    # attempts: 1..N
    if attempts <= 1:
        return 1
    if attempts == 2:
        return 3
    if attempts == 3:
        return 10
    return 60


def _is_retryable_error(exc: BaseException) -> bool:
    try:
        from aiogram.exceptions import TelegramNetworkError

        if isinstance(exc, TelegramNetworkError):
            return True
    except Exception:
        pass

    try:
        import aiohttp

        if isinstance(exc, (aiohttp.ClientConnectorError, aiohttp.ClientOSError)):
            return True
        if isinstance(exc, aiohttp.ClientError):
            # includes DNS errors
            return True
    except Exception:
        pass

    return isinstance(exc, (asyncio.TimeoutError, OSError))


async def enqueue_purchase_notify(*, purchase_id: int) -> None:
    pid = int(purchase_id)
    if pid <= 0:
        return

    async with get_async_session() as session:
        row = TelegramOutbox(
            kind="purchase_chat_notify",
            payload={"purchase_id": int(pid)},
            status="pending",
            attempts=0,
            next_retry_at=_now(),
        )
        session.add(row)
        await session.flush()


async def _send_purchase_notify_once(*, purchase_id: int) -> None:
    # Reuse web-side after-commit notifier implementation (already reloads purchase from DB and saves tg link).
    from web.app.main import _notify_purchases_chat_status_after_commit

    await _notify_purchases_chat_status_after_commit(purchase_id=int(purchase_id))


async def process_outbox_batch(*, limit: int = 20) -> None:
    lim = max(1, int(limit))
    now = _now()

    async with get_async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(TelegramOutbox)
                    .where(TelegramOutbox.status == "pending")
                    .where(or_(TelegramOutbox.next_retry_at == None, TelegramOutbox.next_retry_at <= now))
                    .order_by(TelegramOutbox.id.asc())
                    .limit(lim)
                )
            )
            .scalars()
            .all()
        )

        if not rows:
            return

        for row in rows:
            payload = row.payload or {}
            kind = str(row.kind or "").strip()
            if kind != "purchase_chat_notify":
                row.status = "failed"
                row.last_error = "unknown kind"
                continue

            pid = int(payload.get("purchase_id") or 0)
            if pid <= 0:
                row.status = "failed"
                row.last_error = "bad purchase_id"
                continue

            row.attempts = int(row.attempts or 0) + 1
            try:
                await _send_purchase_notify_once(purchase_id=pid)
                row.status = "sent"
                row.last_error = None
                row.next_retry_at = None
                _logger.info("[tg_outbox] sent", extra={"outbox_id": int(row.id), "purchase_id": int(pid), "attempts": int(row.attempts)})
            except Exception as e:
                retryable = _is_retryable_error(e)
                if retryable and int(row.attempts) < 10:
                    delay_s = _next_delay(int(row.attempts))
                    row.status = "pending"
                    row.next_retry_at = now + timedelta(seconds=int(delay_s))
                else:
                    row.status = "failed"
                    row.next_retry_at = None
                row.last_error = (str(e) or type(e).__name__)[:2000]
                _logger.exception(
                    "[tg_outbox] send failed",
                    extra={
                        "outbox_id": int(row.id),
                        "purchase_id": int(pid),
                        "attempts": int(row.attempts),
                        "retryable": bool(retryable),
                    },
                )

        await session.flush()


async def telegram_outbox_job() -> None:
    # quick guard
    if not str(getattr(settings, "BOT_TOKEN", "") or "").strip():
        return

    try:
        await process_outbox_batch(limit=25)
    except Exception:
        _logger.exception("[tg_outbox] job failed")
