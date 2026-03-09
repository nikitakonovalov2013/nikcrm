from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Optional

from sqlalchemy import and_, case, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enums import ShiftInstanceStatus
from shared.models import ShiftInstance, User, WorkShiftDay
from shared.services.shifts_domain import is_shift_active_status, is_shift_final_status


@dataclass(frozen=True)
class TodayWorkingStaffRow:
    user_id: int
    full_name: str
    start_time: Optional[time]
    end_time: Optional[time]
    planned_hours: Optional[int]
    is_opened: bool


@dataclass(frozen=True)
class ShiftForDateRow:
    user_id: int
    full_name: str
    start_time: Optional[time]
    end_time: Optional[time]
    planned_hours: Optional[int]
    shift_id: Optional[int]
    status: Optional[str]
    started_at: Optional[object]
    ended_at: Optional[object]
    opened: bool
    finished: bool


async def get_today_working_staff_with_open_state(
    *,
    session: AsyncSession,
    day: date,
) -> list[TodayWorkingStaffRow]:
    """Return staff who have a WORK plan for the given day (exclude off-days).

    is_opened=True if user has a shift instance for that day with started_at set
    or status == STARTED.

    This function is used by bot/web and should not rely on Telegram IDs.
    """

    opened_expr = case(
        (
            and_(
                ShiftInstance.id.is_not(None),
                (
                    (ShiftInstance.status == ShiftInstanceStatus.STARTED)
                    | (ShiftInstance.started_at.is_not(None))
                ),
            ),
            literal(True),
        ),
        else_=literal(False),
    )

    q = (
        select(
            User.id,
            User.first_name,
            User.last_name,
            WorkShiftDay.start_time,
            WorkShiftDay.end_time,
            WorkShiftDay.hours,
            opened_expr.label("is_opened"),
        )
        .join(User, User.id == WorkShiftDay.user_id)
        .outerjoin(
            ShiftInstance,
            and_(ShiftInstance.user_id == WorkShiftDay.user_id, ShiftInstance.day == WorkShiftDay.day),
        )
        .where(WorkShiftDay.day == day)
        .where(WorkShiftDay.kind == "work")
        .where(User.is_deleted == False)  # noqa: E712
        .order_by(WorkShiftDay.start_time.asc().nullslast(), User.last_name.asc().nullslast(), User.first_name.asc().nullslast(), User.id.asc())
    )

    rows = (await session.execute(q)).all()

    out: list[TodayWorkingStaffRow] = []
    for user_id, first_name, last_name, start_time, end_time, hours, is_opened in rows:
        fio = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()
        out.append(
            TodayWorkingStaffRow(
                user_id=int(user_id),
                full_name=fio or f"#{int(user_id)}",
                start_time=start_time,
                end_time=end_time,
                planned_hours=int(hours) if hours is not None else None,
                is_opened=bool(is_opened),
            )
        )

    return out


async def get_shifts_for_date(*, session: AsyncSession, day: date) -> list[ShiftForDateRow]:
    """Return schedule rows for a day with user data and opened/finished flags.

    The result is based on planned WORK days. If a user has multiple ShiftInstance
    rows for the same day, each instance is returned as a separate row.
    """

    plans_rows = (
        await session.execute(
            select(
                WorkShiftDay.user_id,
                User.first_name,
                User.last_name,
                WorkShiftDay.start_time,
                WorkShiftDay.end_time,
                WorkShiftDay.hours,
            )
            .join(User, User.id == WorkShiftDay.user_id)
            .where(WorkShiftDay.day == day)
            .where(WorkShiftDay.kind == "work")
            .where(User.is_deleted == False)  # noqa: E712
            .order_by(WorkShiftDay.start_time.asc().nullslast(), User.last_name.asc().nullslast(), User.first_name.asc().nullslast(), User.id.asc())
        )
    ).all()

    if not plans_rows:
        return []

    user_ids = [int(r[0]) for r in plans_rows]
    shifts_rows = (
        await session.execute(
            select(
                ShiftInstance.id,
                ShiftInstance.user_id,
                ShiftInstance.status,
                ShiftInstance.started_at,
                ShiftInstance.ended_at,
            )
            .where(ShiftInstance.day == day)
            .where(ShiftInstance.user_id.in_(user_ids))
            .order_by(ShiftInstance.user_id.asc(), ShiftInstance.id.asc())
        )
    ).all()

    shifts_by_user: dict[int, list[tuple[int, object, object, object]]] = {}
    for sid, uid, status, started_at, ended_at in shifts_rows:
        uid_i = int(uid or 0)
        if uid_i <= 0:
            continue
        shifts_by_user.setdefault(uid_i, []).append((int(sid), status, started_at, ended_at))

    out: list[ShiftForDateRow] = []
    for user_id, first_name, last_name, start_time, end_time, hours in plans_rows:
        uid_i = int(user_id)
        fio = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip()
        full_name = fio or f"#{uid_i}"
        rows = shifts_by_user.get(uid_i) or []
        if not rows:
            out.append(
                ShiftForDateRow(
                    user_id=uid_i,
                    full_name=full_name,
                    start_time=start_time,
                    end_time=end_time,
                    planned_hours=(int(hours) if hours is not None else None),
                    shift_id=None,
                    status=None,
                    started_at=None,
                    ended_at=None,
                    opened=False,
                    finished=False,
                )
            )
            continue

        for sid, status, started_at, ended_at in rows:
            status_s = str(status or "")
            opened = bool(
                started_at is not None
                or is_shift_active_status(status)
                or is_shift_final_status(status, ended_at=ended_at)
                or status_s in {ShiftInstanceStatus.STARTED, ShiftInstanceStatus.CLOSED, ShiftInstanceStatus.APPROVED}
            )
            finished = bool(is_shift_final_status(status, ended_at=ended_at))
            out.append(
                ShiftForDateRow(
                    user_id=uid_i,
                    full_name=full_name,
                    start_time=start_time,
                    end_time=end_time,
                    planned_hours=(int(hours) if hours is not None else None),
                    shift_id=int(sid),
                    status=(status_s or None),
                    started_at=started_at,
                    ended_at=ended_at,
                    opened=opened,
                    finished=finished,
                )
            )

    return out
