from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Optional

from sqlalchemy import and_, case, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.enums import ShiftInstanceStatus
from shared.models import ShiftInstance, User, WorkShiftDay


@dataclass(frozen=True)
class TodayWorkingStaffRow:
    user_id: int
    full_name: str
    start_time: Optional[time]
    end_time: Optional[time]
    planned_hours: Optional[int]
    is_opened: bool


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
