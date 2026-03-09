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
    rating: int | None
    rated_at: object | None
    needs_review: bool
    confirmed_at: object | None
    confirmed_by_user_id: int | None

    # Backward-compatible name: previously hourly rate, now interpreted as per-shift base rate
    hour_rate: Decimal | None

    requested_amount: Decimal | None
    approved_amount: Decimal | None
    is_amount_approved: bool

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
    rating: int | None,
    rated_at=None,
    manual_hours: Decimal | None,
    manual_amount_override: Decimal | None,
    requested_amount: Decimal | None,
    approved_amount: Decimal | None,
    approval_required: bool | None,
    approved_at=None,
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

    # Requested amount approval rules:
    # - base rate is hour_rate (per-shift) from profile
    # - requested_amount is what employee entered
    # - approved_amount is authoritative approval (if exists)
    base_rate = q2(Decimal(hour_rate)) if hour_rate is not None else DEC_0
    req_amt = requested_amount
    appr_amt = approved_amount
    try:
        if req_amt is not None and abs(Decimal(req_amt)) <= Decimal("0.0001"):
            req_amt = None
    except Exception:
        pass
    try:
        if appr_amt is not None and abs(Decimal(appr_amt)) <= Decimal("0.0001"):
            appr_amt = None
    except Exception:
        pass

    approved_flag = bool(appr_amt is not None) and bool(getattr(approved_at, "__class__", None) is not None or approved_at is not None)
    if approval_required is not None:
        approved_flag = bool(approved_flag and (not bool(approval_required)))

    req_differs = False
    try:
        if req_amt is not None:
            req_differs = bool(q2(Decimal(req_amt)) != base_rate)
    except Exception:
        req_differs = True

    needs_review_requested = bool(req_amt is not None and req_differs and (not approved_flag))

    needs_review_base = bool(
        (state != SalaryShiftState.WORKED)
        or (mh is not None)
        or (mao is not None)
        or (adjustments_amount is not None and abs(Decimal(adjustments_amount)) > Decimal("0.0001"))
        or needs_review_requested
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
            rating=(int(rating) if rating is not None else None),
            rated_at=rated_at,
            needs_review=bool(needs_review),
            confirmed_at=confirmed_at,
            confirmed_by_user_id=(int(confirmed_by_user_id) if confirmed_by_user_id is not None else None),
            hour_rate=hour_rate,
            requested_amount=(q2(Decimal(req_amt)) if req_amt is not None else None),
            approved_amount=(q2(Decimal(appr_amt)) if appr_amt is not None else None),
            is_amount_approved=bool(approved_flag),
            base_amount=q2(base_amount),
            adjustments_amount=q2(adjustments_amount or DEC_0),
            total_amount=total,
        )

    base_amount = DEC_0
    if mao is not None:
        base_amount = mao
    else:
        # New model: hour_rate is interpreted as per-shift base rate.
        # If employee requested a different amount, we use it only after approval.
        if req_amt is not None and approved_flag:
            base_amount = Decimal(req_amt)
        else:
            base_amount = base_rate

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
        rating=(int(rating) if rating is not None else None),
        rated_at=rated_at,
        needs_review=bool(needs_review),
        confirmed_at=confirmed_at,
        confirmed_by_user_id=(int(confirmed_by_user_id) if confirmed_by_user_id is not None else None),
        hour_rate=hour_rate,
        requested_amount=(q2(Decimal(req_amt)) if req_amt is not None else None),
        approved_amount=(q2(Decimal(appr_amt)) if appr_amt is not None else None),
        is_amount_approved=bool(approved_flag),
        base_amount=base_amount,
        adjustments_amount=adjustments_amount,
        total_amount=total,
    )
