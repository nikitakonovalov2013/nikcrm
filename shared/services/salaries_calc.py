from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from datetime import date

from shared.enums import SalaryShiftState, ShiftInstanceStatus


DEC_0 = Decimal("0")
DEC_2PL = Decimal("0.01")


def q2(v: Decimal) -> Decimal:
    return (v or DEC_0).quantize(DEC_2PL, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SalaryShiftCalc:
    shift_id: int
    user_id: int
    day: date

    planned_hours: Decimal | None
    actual_hours: Decimal | None

    state: SalaryShiftState
    needs_review: bool
    confirmed_at: object | None
    confirmed_by_user_id: int | None

    hour_rate: Decimal | None

    base_amount: Decimal
    adjustments_amount: Decimal
    total_amount: Decimal


def calc_actual_hours(*, started_at, ended_at) -> Decimal | None:
    if started_at is None or ended_at is None:
        return None
    try:
        sec = (ended_at - started_at).total_seconds()
        if sec <= 0:
            return None
        return (Decimal(sec) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return None


def calc_shift_salary(
    *,
    shift_id: int,
    user_id: int,
    day: date,
    hour_rate: Decimal | None,
    planned_hours: Decimal | None,
    shift_status: object | None,
    started_at,
    ended_at,
    state: SalaryShiftState,
    manual_hours: Decimal | None,
    manual_amount_override: Decimal | None,
    adjustments_amount: Decimal,
    confirmed_at=None,
    confirmed_by_user_id: int | None = None,
) -> SalaryShiftCalc:
    st = str(shift_status.value if hasattr(shift_status, "value") else str(shift_status or ""))

    actual_hours = calc_actual_hours(started_at=started_at, ended_at=ended_at)

    mh = manual_hours
    mao = manual_amount_override
    try:
        if mh is not None and abs(Decimal(mh)) <= Decimal("0.0001"):
            mh = None
    except Exception:
        pass
    try:
        if mao is not None and abs(Decimal(mao)) <= Decimal("0.0001"):
            mao = None
    except Exception:
        pass

    needs_review_base = bool(
        (state != SalaryShiftState.WORKED)
        or (mh is not None)
        or (mao is not None)
        or (adjustments_amount is not None and abs(Decimal(adjustments_amount)) > Decimal("0.0001"))
    )

    needs_review = bool(needs_review_base and (confirmed_at is None))

    # skip/day_off => 0
    if state in {SalaryShiftState.DAY_OFF, SalaryShiftState.SKIP}:
        base_amount = DEC_0
        total = q2(base_amount + (adjustments_amount or DEC_0))
        return SalaryShiftCalc(
            shift_id=int(shift_id),
            user_id=int(user_id),
            day=day,
            planned_hours=planned_hours,
            actual_hours=actual_hours,
            state=state,
            needs_review=bool(needs_review),
            confirmed_at=confirmed_at,
            confirmed_by_user_id=(int(confirmed_by_user_id) if confirmed_by_user_id is not None else None),
            hour_rate=hour_rate,
            base_amount=q2(base_amount),
            adjustments_amount=q2(adjustments_amount or DEC_0),
            total_amount=total,
        )

    # choose hours for base
    effective_hours: Decimal | None = None
    if mh is not None:
        effective_hours = mh
    elif actual_hours is not None and not needs_review:
        # if we have actual hours and no anomalies -> use them
        effective_hours = actual_hours
    else:
        effective_hours = planned_hours

    base_amount = DEC_0
    if mao is not None:
        base_amount = mao
    else:
        if hour_rate is not None and effective_hours is not None:
            base_amount = hour_rate * effective_hours

    base_amount = q2(base_amount)
    adjustments_amount = q2(adjustments_amount or DEC_0)
    total = q2(base_amount + adjustments_amount)

    return SalaryShiftCalc(
        shift_id=int(shift_id),
        user_id=int(user_id),
        day=day,
        planned_hours=planned_hours,
        actual_hours=actual_hours,
        state=state,
        needs_review=bool(needs_review),
        confirmed_at=confirmed_at,
        confirmed_by_user_id=(int(confirmed_by_user_id) if confirmed_by_user_id is not None else None),
        hour_rate=hour_rate,
        base_amount=base_amount,
        adjustments_amount=adjustments_amount,
        total_amount=total,
    )
