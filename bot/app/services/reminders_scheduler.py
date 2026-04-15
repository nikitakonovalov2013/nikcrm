from __future__ import annotations

import logging
from datetime import datetime, date, time
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, func

from shared.config import settings
from shared.db import get_async_session
from shared.enums import Position, ShiftInstanceStatus, UserStatus
from shared.models import MaterialSupply, MaterialConsumption, User, WorkShiftDay, ShiftInstance
from shared.services.magic_links import create_magic_token
from shared.services.shifts_domain import calc_shift_default_amount, format_hours_from_times_int, is_shift_active_status, is_shift_final_status
from shared.utils import utc_now
from bot.app.utils.urls import get_schedule_url
from bot.app.repository.reminders_settings import ReminderSettingsRepository
from bot.app.services.stocks_reports import build_report
from bot.app.services.stocks_reports_format import format_report_html
from bot.app.services.telegram_outbox import telegram_outbox_job


_logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(getattr(settings, "TIMEZONE", "Europe/Moscow"))
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _wsd_effective_times(wsd: WorkShiftDay) -> tuple[time, time]:
    st = getattr(wsd, "start_time", None) or time(10, 0)
    et = getattr(wsd, "end_time", None) or time(18, 0)
    return st, et


def _dt_msk_for_day_time(day: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime(
        year=day.year,
        month=day.month,
        day=day.day,
        hour=t.hour,
        minute=t.minute,
        second=0,
        microsecond=0,
        tzinfo=tz,
    )


async def shift_time_notifications_job() -> None:
    tz = _tz()
    now = datetime.now(tz)
    today = now.date()

    _logger.info("shift_time_notifications_job tick", extra={"now": now.isoformat(), "day": str(today)})

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        async with get_async_session() as session:
            rows = list(
                (
                    await session.execute(
                        select(WorkShiftDay, User)
                        .join(User, User.id == WorkShiftDay.user_id)
                        .where(WorkShiftDay.day == today)
                        .where(WorkShiftDay.kind == "work")
                        .where(User.is_deleted == False)
                        .where(User.status == UserStatus.APPROVED)
                    )
                ).all()
            )

            for wsd, u in rows:
                chat_id = int(getattr(u, "tg_id", 0) or 0)
                if not chat_id:
                    continue

                # Do not send start reminders if there is already a factual shift (started or finished) for today.
                shift = (
                    await session.execute(
                        select(ShiftInstance)
                        .where(ShiftInstance.user_id == int(getattr(u, "id")))
                        .where(ShiftInstance.day == today)
                    )
                ).scalar_one_or_none()
                shift_exists_block_start = False
                if shift is not None:
                    if is_shift_active_status(getattr(shift, "status", None), ended_at=getattr(shift, "ended_at", None)):
                        shift_exists_block_start = True
                    if is_shift_final_status(getattr(shift, "status", None), ended_at=getattr(shift, "ended_at", None)):
                        shift_exists_block_start = True

                # Do not send end reminders if factual shift is already finished/final.
                shift_exists_block_end = False
                if shift is not None:
                    if is_shift_final_status(getattr(shift, "status", None), ended_at=getattr(shift, "ended_at", None)):
                        shift_exists_block_end = True

                st, et = _wsd_effective_times(wsd)
                start_dt = _dt_msk_for_day_time(today, st, tz)
                end_dt = _dt_msk_for_day_time(today, et, tz)

                # START notification
                if (not shift_exists_block_start) and getattr(wsd, "start_notified_at", None) is None and now >= start_dt:
                    hrs = format_hours_from_times_int(start_time=st, end_time=et)
                    text = (
                        f"⏰ <b>Начало смены</b>\n\n"
                        f"Сегодня у тебя смена: <b>{st.strftime('%H:%M')}–{et.strftime('%H:%M')}</b> ({hrs} часов).\n"
                        f"Начать смену?"
                    )
                    kb = {
                        "inline_keyboard": [
                            [{"text": "✅ Начать", "callback_data": f"shift:start:{today.isoformat()}"}],
                            [{"text": "📅 Меню графика", "callback_data": "sched_menu:open"}],
                        ]
                    }
                    try:
                        await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
                        wsd.start_notified_at = now
                        await session.flush()
                        _logger.info("shift start notified", extra={"user_id": int(getattr(u, 'id')), "wsd_id": int(getattr(wsd, 'id'))})
                    except Exception:
                        _logger.exception("failed to send shift start notification", extra={"chat_id": chat_id})

                # END notification — prompt removed; auto-close is handled by shift_auto_close_job.
                if getattr(wsd, "end_notified_at", None) is None and now >= end_dt:
                    wsd.end_notified_at = now
                    wsd.end_snooze_until = None
                    wsd.end_followup_notified_at = None
                    await session.flush()

    finally:
        await bot.session.close()


async def shift_auto_close_job() -> None:
    tz = _tz()
    now = datetime.now(tz)
    now_utc = utc_now()
    today = now.date()

    _logger.info("shift_auto_close_job tick", extra={"now": now.isoformat(), "day": str(today)})

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        async with get_async_session() as session:
            rows = list(
                (
                    await session.execute(
                        select(WorkShiftDay, User)
                        .join(User, User.id == WorkShiftDay.user_id)
                        .where(WorkShiftDay.day == today)
                        .where(WorkShiftDay.kind == "work")
                        .where(User.is_deleted == False)
                        .where(User.status == UserStatus.APPROVED)
                    )
                ).all()
            )

            for wsd, u in rows:
                chat_id = int(getattr(u, "tg_id", 0) or 0)
                if not chat_id:
                    continue

                shift = (
                    await session.execute(
                        select(ShiftInstance)
                        .where(ShiftInstance.user_id == int(getattr(u, "id")))
                        .where(ShiftInstance.day == today)
                        .order_by(ShiftInstance.id.desc())
                    )
                ).scalars().first()

                if shift is None:
                    continue

                # Idempotency: do nothing if already finished.
                if is_shift_final_status(getattr(shift, "status", None), ended_at=getattr(shift, "ended_at", None)):
                    continue
                if not is_shift_active_status(getattr(shift, "status", None), ended_at=getattr(shift, "ended_at", None)):
                    continue

                st, et = _wsd_effective_times(wsd)
                end_dt = _dt_msk_for_day_time(today, et, tz)
                if now < end_dt:
                    continue

                # Respect user snooze (if they clicked "Ещё работаю")
                snooze_until = getattr(wsd, "end_snooze_until", None)
                if snooze_until is not None and now < snooze_until.astimezone(tz):
                    continue

                base_rate = int(getattr(shift, "base_rate", None) or int(getattr(u, "rate_k", 0) or 0))
                extra_hours = int(getattr(shift, "extra_hours", 0) or 0)
                overtime_hours = int(getattr(shift, "overtime_hours", 0) or 0)
                extra_rate = int(getattr(shift, "extra_hour_rate", 300) or 300)
                overtime_rate = int(getattr(shift, "overtime_hour_rate", 400) or 400)
                amount_default = int(
                    getattr(shift, "amount_default", 0)
                    or calc_shift_default_amount(
                        base_rate=base_rate,
                        extra_hours=extra_hours,
                        extra_hour_rate=extra_rate,
                        overtime_hours=overtime_hours,
                        overtime_hour_rate=overtime_rate,
                    )
                )

                shift.base_rate = base_rate
                shift.amount_default = amount_default
                shift.amount_submitted = amount_default
                shift.amount_approved = amount_default
                shift.approval_required = False
                shift.status = ShiftInstanceStatus.APPROVED
                shift.ended_at = now_utc
                await session.flush()

                text = f"✅ Смена завершена, ваш баланс пополнен на <b>{int(amount_default)} ₽</b>."
                kb = {
                    "inline_keyboard": [
                        [{"text": "✏️ Изменить сумму", "callback_data": f"shift:close_edit:{int(getattr(shift, 'id'))}"}],
                    ]
                }

                try:
                    await bot.send_message(chat_id=int(chat_id), text=text, reply_markup=kb)
                except Exception:
                    _logger.exception("failed to send auto shift close message", extra={"chat_id": int(chat_id)})
    finally:
        await bot.session.close()


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


async def _has_any_operations_today(now: datetime) -> bool:
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    async with get_async_session() as session:
        s_cnt = await session.execute(
            select(func.count()).select_from(MaterialSupply).where(MaterialSupply.created_at >= start).where(MaterialSupply.created_at <= end)
        )
        c_cnt = await session.execute(
            select(func.count()).select_from(MaterialConsumption).where(MaterialConsumption.created_at >= start).where(MaterialConsumption.created_at <= end)
        )
        return (s_cnt.scalar_one() or 0) > 0 or (c_cnt.scalar_one() or 0) > 0


async def _recipient_tg_ids(send_admins: bool, send_managers: bool) -> list[int]:
    ids: set[int] = set()
    if send_admins:
        ids.update(int(x) for x in settings.admin_ids)
    if send_managers:
        async with get_async_session() as session:
            res = await session.execute(
                select(User.tg_id)
                .where(User.status == UserStatus.APPROVED)
                .where(User.position == Position.MANAGER)
            )
            ids.update(int(r[0]) for r in res.all())
    return sorted(ids)


async def reminder_job() -> None:
    tz = _tz()
    now = datetime.now(tz)

    _logger.info("reminder_job tick", extra={"now": now.isoformat()})

    async with get_async_session() as session:
        repo = ReminderSettingsRepository(session)
        s = await repo.get_singleton()

    if not getattr(settings, "REMINDERS_ENABLED", True):
        _logger.info("reminders disabled by env")
        return
    if not s.reminders_enabled:
        _logger.info("reminders disabled in db")
        return
    if s.skip_weekends and _is_weekend(now.date()):
        _logger.info("reminder skipped weekend")
        return

    if await _has_any_operations_today(now):
        _logger.info("reminder skipped: operations exist")
        return

    ids = await _recipient_tg_ids(s.send_to_admins, s.send_to_managers)
    if not ids and getattr(settings, "REPORTS_CHAT_ID", 0):
        ids = [int(settings.REPORTS_CHAT_ID)]

    if not ids:
        _logger.info("reminder has no recipients")
        return

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    text = "⏰ <b>Напоминание</b>\n\nСегодня ещё не внесены операции по складу (расход/пополнение)." 
    for chat_id in ids:
        try:
            await bot.send_message(chat_id=chat_id, text=text)
            _logger.info("reminder sent", extra={"chat_id": chat_id})
        except Exception:
            _logger.exception("failed to send reminder", extra={"chat_id": chat_id})
    await bot.session.close()


async def shifts_morning_job() -> None:
    tz = _tz()
    now = datetime.now(tz)
    today = now.date()

    _logger.info("shifts_morning_job tick", extra={"now": now.isoformat(), "day": str(today)})

    async with get_async_session() as session:
        rows = list(
            (
                await session.execute(
                    select(WorkShiftDay, User)
                    .join(User, User.id == WorkShiftDay.user_id)
                    .where(WorkShiftDay.day == today)
                    .where(WorkShiftDay.kind == "work")
                    .where(User.is_deleted == False)
                    .where(User.status == UserStatus.APPROVED)
                )
            ).all()
        )

        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        try:
            for wsd, u in rows:
                chat_id = int(getattr(u, "tg_id", 0) or 0)
                if not chat_id:
                    continue

                hours = getattr(wsd, "hours", None)
                h_txt = f" ({int(hours)}ч)" if hours else ""

                tok = await create_magic_token(session, user_id=int(getattr(u, "id")), ttl_minutes=60, scope="schedule")
                link = f"{get_schedule_url(is_admin=False, is_manager=False)}"
                # Use tg-auth route to set cookie
                base = str(getattr(settings, "INTERNAL_WEB_BASE_URL", "") or "").strip() or "http://web:8000"
                if base.endswith("/"):
                    base = base[:-1]
                web_link = base + f"/crm/auth/tg?t={tok}&next=%2Fcrm%2Fschedule%2Fpublic&scope=schedule"

                text = (
                    f"⏰ <b>Смена сегодня</b>\n\n"
                    f"Сегодня у вас смена{h_txt}.\n"
                    f"Откройте меню графика, чтобы начать или закрыть смену.\n"
                    f"\n🔗 График: {web_link}"
                )

                kb = {
                    "inline_keyboard": [
                        [
                            {"text": "✅ Начать смену", "callback_data": f"shift:start:{today.isoformat()}"},
                        ],
                        [
                            {"text": "📅 Меню графика", "callback_data": "sched_menu:open"},
                        ],
                        [
                            {"text": "🔗 Открыть график", "url": web_link},
                        ],
                    ]
                }
                try:
                    await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)
                except Exception:
                    _logger.exception("failed to send shift morning msg", extra={"chat_id": chat_id})
        finally:
            await bot.session.close()


async def daily_report_job() -> None:
    tz = _tz()
    now = datetime.now(tz)

    _logger.info("daily_report_job tick", extra={"now": now.isoformat()})

    async with get_async_session() as session:
        repo = ReminderSettingsRepository(session)
        s = await repo.get_singleton()

    if not s.daily_report_enabled:
        _logger.info("daily report disabled")
        return

    chat_id = int(getattr(settings, "REPORTS_CHAT_ID", 0) or 0)
    if not chat_id:
        _logger.info("daily report skipped: REPORTS_CHAT_ID not set")
        return

    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)

    async with get_async_session() as session:
        data = await build_report(session, start=start, end=end, events_limit=10)

    text = format_report_html("Авто-отчёт за сегодня", data)

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        await bot.send_message(chat_id=chat_id, text=text)
        _logger.info("daily report sent", extra={"chat_id": chat_id})
    except Exception:
        _logger.exception("failed to send daily report", extra={"chat_id": chat_id})
    await bot.session.close()


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            timezone=_tz(),
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 60,
            },
        )

        def _listener(event) -> None:
            try:
                job_id = getattr(event, "job_id", None)
                if event.exception:
                    _logger.error(
                        "scheduler job error",
                        extra={"job_id": job_id, "exception": repr(getattr(event, "exception", None))},
                    )
                else:
                    _logger.info("scheduler job executed", extra={"job_id": job_id})
            except Exception:
                pass

        _scheduler.add_listener(_listener, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    return _scheduler


def schedule_jobs() -> None:
    sched = get_scheduler()
    if getattr(sched, "_nikcrm_jobs_added", False):
        _logger.info("schedule_jobs skipped: already added")
        return
    setattr(sched, "_nikcrm_jobs_added", True)

    tz = _tz()
    sched.add_job(
        reminder_job,
        CronTrigger(hour=16, minute=0, timezone=tz),
        id="stocks_reminder",
        replace_existing=True,
    )
    sched.add_job(
        daily_report_job,
        CronTrigger(hour=18, minute=0, timezone=tz),
        id="stocks_daily_report",
        replace_existing=True,
    )

    sched.add_job(
        shifts_morning_job,
        CronTrigger(hour=8, minute=0, timezone=tz),
        id="shifts_morning",
        replace_existing=True,
    )

    # Shift start/end notifications by planned time (polling)
    sched.add_job(
        shift_time_notifications_job,
        IntervalTrigger(minutes=1, timezone=tz),
        id="shift_time_notifications",
        replace_existing=True,
    )

    # Shift auto close by planned end time (polling)
    sched.add_job(
        shift_auto_close_job,
        IntervalTrigger(minutes=1, timezone=tz),
        id="shift_auto_close",
        replace_existing=True,
    )

    # Telegram outbox retry (best-effort) for network/DNS issues
    sched.add_job(
        telegram_outbox_job,
        IntervalTrigger(seconds=30, timezone=tz),
        id="telegram_outbox",
        replace_existing=True,
    )

    _logger.info(
        "scheduler default jobs scheduled",
        extra={
            "tz": str(tz),
            "reminder_next": str(getattr(sched.get_job('stocks_reminder'), 'next_run_time', None)),
            "daily_next": str(getattr(sched.get_job('stocks_daily_report'), 'next_run_time', None)),
        },
    )


async def reschedule_from_db() -> None:
    tz = _tz()
    try:
        async with get_async_session() as session:
            repo = ReminderSettingsRepository(session)
            s = await repo.get_singleton()
    except Exception:
        _logger.exception("failed to load reminder settings from db")
        raise

    sched = get_scheduler()

    sched.add_job(
        reminder_job,
        CronTrigger(hour=s.reminder_time.hour, minute=s.reminder_time.minute, timezone=tz),
        id="stocks_reminder",
        replace_existing=True,
    )
    sched.add_job(
        daily_report_job,
        CronTrigger(hour=s.daily_report_time.hour, minute=s.daily_report_time.minute, timezone=tz),
        id="stocks_daily_report",
        replace_existing=True,
    )

    # Shift notifications: fixed at 08:00 by default (can be made configurable later)
    sched.add_job(
        shifts_morning_job,
        CronTrigger(hour=8, minute=0, timezone=tz),
        id="shifts_morning",
        replace_existing=True,
    )

    sched.add_job(
        shift_time_notifications_job,
        IntervalTrigger(minutes=1, timezone=tz),
        id="shift_time_notifications",
        replace_existing=True,
    )

    sched.add_job(
        shift_auto_close_job,
        IntervalTrigger(minutes=1, timezone=tz),
        id="shift_auto_close",
        replace_existing=True,
    )

    _logger.info(
        "scheduler jobs rescheduled from db",
        extra={
            "tz": str(tz),
            "reminder_time": f"{s.reminder_time.hour:02d}:{s.reminder_time.minute:02d}",
            "daily_time": f"{s.daily_report_time.hour:02d}:{s.daily_report_time.minute:02d}",
            "reminder_next": str(getattr(sched.get_job('stocks_reminder'), 'next_run_time', None)),
            "daily_next": str(getattr(sched.get_job('stocks_daily_report'), 'next_run_time', None)),
        },
    )

    # Extra diagnostics: list all jobs once after reschedule
    try:
        _logger.info(
            "scheduler jobs snapshot",
            extra={
                "jobs": [
                    {"id": j.id, "next": str(getattr(j, "next_run_time", None))}
                    for j in sched.get_jobs()
                ]
            },
        )
    except Exception:
        pass


def start_scheduler() -> None:
    sched = get_scheduler()
    if sched.running:
        _logger.info("scheduler already running")
        return
    schedule_jobs()
    sched.start()
    _logger.info(
        "scheduler started",
        extra={
            "tz": str(getattr(sched, "timezone", None)),
            "jobs": [j.id for j in sched.get_jobs()],
        },
    )
