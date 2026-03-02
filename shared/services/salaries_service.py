from __future__ import annotations

import asyncio
import html
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from shared.config import settings
from shared.enums import SalaryShiftState, ShiftInstanceStatus
from shared.models import (
    User,
    WorkShiftDay,
    ShiftInstance,
    SalarySettings,
    SalaryShiftStateRow,
    SalaryAdjustment,
    SalaryPayout,
    SalaryShiftAudit,
    SalaryPayoutAudit,
)
from shared.services.salaries_calc import calc_shift_salary, q2, DEC_0
from shared.services.salaries_pin import get_salary_settings
from shared.utils import utc_now


_TG_SALARY_AGG_WINDOW_SEC = 30
_tg_salary_agg_lock: asyncio.Lock | None = None
_tg_salary_agg: dict[int, dict] = {}


def _tg_salary_get_lock() -> asyncio.Lock:
    global _tg_salary_agg_lock
    if _tg_salary_agg_lock is None:
        _tg_salary_agg_lock = asyncio.Lock()
    return _tg_salary_agg_lock


async def _tg_salary_agg_flush(chat_id: int) -> None:
    try:
        await asyncio.sleep(int(_TG_SALARY_AGG_WINDOW_SEC))
        lock = _tg_salary_get_lock()
        async with lock:
            st = _tg_salary_agg.get(int(chat_id)) or {}
            lines = list(st.get("lines") or [])
            _tg_salary_agg.pop(int(chat_id), None)
        if not lines:
            return
        txt = "🧾 <b>Изменения по зарплате</b>\n\n" + "\n".join(lines)
        await _tg_send_html(chat_id=int(chat_id), text=txt)
    except Exception:
        try:
            lock = _tg_salary_get_lock()
            async with lock:
                _tg_salary_agg.pop(int(chat_id), None)
        except Exception:
            pass


async def _tg_salary_enqueue(chat_id: int, line_html: str) -> None:
    try:
        lock = _tg_salary_get_lock()
        async with lock:
            st = _tg_salary_agg.get(int(chat_id))
            if st is None:
                st = {"lines": [], "task": None}
                _tg_salary_agg[int(chat_id)] = st
            st["lines"].append(str(line_html))
            t = st.get("task")
            if t is None or getattr(t, "done", lambda: True)():
                st["task"] = asyncio.create_task(_tg_salary_agg_flush(int(chat_id)))
    except Exception:
        pass

@dataclass(frozen=True)
class SalaryPeriodTotals:
    accrued: Decimal
    paid: Decimal
    balance: Decimal
    needs_review_total: int


async def get_balance_cutoff_date(*, session: AsyncSession) -> date:
    try:
        st: SalarySettings = await get_salary_settings(session)
        d = getattr(st, "balance_cutoff_date", None)
        if isinstance(d, date):
            return d
    except Exception:
        pass
    return date(2026, 3, 1)


async def _tg_send_html(*, chat_id: int, text: str) -> None:
    token = str(getattr(settings, "BOT_TOKEN", "") or "").strip()
    if not token:
        return
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


def _money(v: Decimal) -> str:
    return f"{q2(v):.2f} ₽"


def _esc(s: str) -> str:
    return html.escape(str(s or ""))


async def load_user_hour_rate(*, session: AsyncSession, user_id: int) -> Decimal | None:
    row = (
        await session.execute(
            select(User.hour_rate, User.rate_k).where(User.id == int(user_id))
        )
    ).first()
    if not row:
        return None
    hr = row[0]
    if hr is not None:
        return hr
    rk = row[1]
    if rk is None:
        return None
    try:
        return Decimal(int(rk))
    except Exception:
        return None


async def load_user_tg_id(*, session: AsyncSession, user_id: int) -> int | None:
    row = (await session.execute(select(User.tg_id).where(User.id == int(user_id)))).first()
    if not row:
        return None
    tg_id = int(row[0] or 0)
    return tg_id if tg_id > 0 else None


async def calc_user_period_totals(
    *,
    session: AsyncSession,
    user_id: int,
    period_start: date,
    period_end: date,
) -> SalaryPeriodTotals:
    # Month accrual: only worked/closed (or manually confirmed) shifts.
    items_month = await calc_user_shifts(
        session=session,
        user_id=user_id,
        period_start=period_start,
        period_end=period_end,
        include_plans=True,
        only_accruable=True,
    )
    accrued_month = q2(sum((it.total_amount for it in items_month), DEC_0))
    needs_review_total = int(sum((1 for it in items_month if bool(getattr(it, "needs_review", False))), 0))

    paid_month = (
        await session.execute(
            select(func.coalesce(func.sum(SalaryPayout.amount), 0))
            .where(SalaryPayout.user_id == int(user_id))
            .where(SalaryPayout.period_start == period_start)
            .where(SalaryPayout.period_end == period_end)
        )
    ).scalar_one()
    paid_month = q2(Decimal(paid_month))

    cutoff = await get_balance_cutoff_date(session=session)
    all_start = cutoff

    items_all = await calc_user_shifts(
        session=session,
        user_id=user_id,
        period_start=all_start,
        period_end=period_end,
        include_plans=False,
        only_accruable=True,
    )
    accrued_all = q2(sum((it.total_amount for it in items_all), DEC_0))

    paid_all = (
        await session.execute(
            select(func.coalesce(func.sum(SalaryPayout.amount), 0))
            .where(SalaryPayout.user_id == int(user_id))
            .where(func.date(SalaryPayout.created_at) >= cutoff)
        )
    ).scalar_one()
    paid_all = q2(Decimal(paid_all))

    balance = q2(accrued_all - paid_all)
    return SalaryPeriodTotals(accrued=accrued_month, paid=paid_month, balance=balance, needs_review_total=needs_review_total)


async def _ensure_salary_shift_state(
    *,
    session: AsyncSession,
    shift: ShiftInstance,
) -> SalaryShiftStateRow:
    existing = (await session.execute(
        select(SalaryShiftStateRow).where(SalaryShiftStateRow.shift_id == int(shift.id))
    )).scalars().first()
    if existing is not None:
        return existing
    row = SalaryShiftStateRow(
        shift_id=int(shift.id),
        state=SalaryShiftState.WORKED,
        manual_hours=None,
        manual_amount_override=None,
        comment=None,
        is_paid=False,
        updated_by_user_id=None,
    )
    session.add(row)
    await session.flush()
    return row


async def calc_user_shifts(
    *,
    session: AsyncSession,
    user_id: int,
    period_start: date,
    period_end: date,
    include_plans: bool = True,
    only_accruable: bool = False,
) -> list:
    # load shifts + plans
    shifts = list(
        (
            await session.execute(
                select(ShiftInstance)
                .where(ShiftInstance.user_id == int(user_id))
                .where(ShiftInstance.day >= period_start)
                .where(ShiftInstance.day <= period_end)
            )
        )
        .scalars()
        .all()
    )

    plans: dict[int, WorkShiftDay] = {}
    if include_plans:
        plans = {
            int(p.day.toordinal()): p
            for p in list(
                (
                    await session.execute(
                        select(WorkShiftDay)
                        .where(WorkShiftDay.user_id == int(user_id))
                        .where(WorkShiftDay.day >= period_start)
                        .where(WorkShiftDay.day <= period_end)
                    )
                )
                .scalars()
                .all()
            )
        }

    hour_rate = await load_user_hour_rate(session=session, user_id=int(user_id))

    # preload adjustments sums
    adj_rows = (await session.execute(
        select(SalaryAdjustment.shift_id, func.coalesce(func.sum(SalaryAdjustment.delta_amount), 0))
        .where(SalaryAdjustment.shift_id.in_([int(s.id) for s in shifts]))
        .group_by(SalaryAdjustment.shift_id)
    )).all() if shifts else []
    adj_sum = {int(r[0]): Decimal(r[1]) for r in adj_rows}

    out = []
    shifts_by_day: dict[int, ShiftInstance] = {int(getattr(s, "day").toordinal()): s for s in shifts if getattr(s, "day", None) is not None}

    # Include all planned WORK days even without any fact ShiftInstance.
    all_day_keys = set(shifts_by_day.keys()) | {k for k, p in plans.items() if str(getattr(p, "kind", "")) == "work"}

    def _is_accruable_shift(*, s: ShiftInstance, st_row: SalaryShiftStateRow | None) -> bool:
        try:
            if st_row is not None and getattr(st_row, "confirmed_at", None) is not None:
                return True
        except Exception:
            pass
        try:
            if getattr(s, "ended_at", None) is not None:
                return True
        except Exception:
            pass
        try:
            st = getattr(s, "status", None)
            return bool(st in {ShiftInstanceStatus.CLOSED, ShiftInstanceStatus.APPROVED})
        except Exception:
            return False

    for day_key in sorted(all_day_keys):
        plan = plans.get(int(day_key))
        s = shifts_by_day.get(int(day_key))

        planned_hours = None
        if plan is not None and getattr(plan, "hours", None) is not None:
            planned_hours = Decimal(int(getattr(plan, "hours")))
        elif s is not None and getattr(s, "planned_hours", None) is not None:
            planned_hours = Decimal(int(getattr(s, "planned_hours")))

        if s is None:
            if plan is None or str(getattr(plan, "kind", "")) != "work":
                continue
            # Planned but not opened/closed: return a virtual row (shift_id=0)
            if not only_accruable:
                out.append(
                    calc_shift_salary(
                        shift_id=0,
                        user_id=int(user_id),
                        day=getattr(plan, "day"),
                        hour_rate=hour_rate,
                        planned_hours=planned_hours,
                        shift_status=ShiftInstanceStatus.PLANNED,
                        started_at=None,
                        ended_at=None,
                        state=SalaryShiftState.NEEDS_REVIEW,
                        manual_hours=None,
                        manual_amount_override=None,
                        requested_amount=None,
                        approved_amount=None,
                        approval_required=None,
                        approved_at=None,
                        adjustments_amount=DEC_0,
                        confirmed_at=None,
                        confirmed_by_user_id=None,
                    )
                )
            continue

        st_row = await _ensure_salary_shift_state(session=session, shift=s)
        if only_accruable and (not _is_accruable_shift(s=s, st_row=st_row)):
            continue
        out.append(
            calc_shift_salary(
                shift_id=int(s.id),
                user_id=int(user_id),
                day=s.day,
                hour_rate=hour_rate,
                planned_hours=planned_hours,
                shift_status=getattr(s, "status", None),
                started_at=getattr(s, "started_at", None),
                ended_at=getattr(s, "ended_at", None),
                state=getattr(st_row, "state", SalaryShiftState.WORKED),
                manual_hours=getattr(st_row, "manual_hours", None),
                manual_amount_override=getattr(st_row, "manual_amount_override", None),
                requested_amount=(Decimal(int(getattr(s, "amount_submitted", 0) or 0)) if getattr(s, "amount_submitted", None) is not None else None),
                approved_amount=(Decimal(int(getattr(s, "amount_approved", 0) or 0)) if getattr(s, "amount_approved", None) is not None else None),
                approval_required=(bool(getattr(s, "approval_required", False)) if getattr(s, "approval_required", None) is not None else None),
                approved_at=getattr(s, "approved_at", None),
                adjustments_amount=adj_sum.get(int(s.id), DEC_0),
                confirmed_at=getattr(st_row, "confirmed_at", None),
                confirmed_by_user_id=(int(getattr(st_row, "confirmed_by_user_id", 0) or 0) or None),
            )
        )

    return out


async def update_salary_shift_state(
    *,
    session: AsyncSession,
    shift_id: int,
    state: SalaryShiftState,
    manual_hours: Decimal | None,
    manual_amount_override: Decimal | None,
    comment: str | None,
    updated_by_user_id: int | None,
    notify_employee: bool = True,
    period_start: date | None = None,
    period_end: date | None = None,
) -> SalaryShiftStateRow:
    shift = (
        await session.execute(select(ShiftInstance).where(ShiftInstance.id == int(shift_id)))
    ).scalars().first()
    if shift is None:
        raise ValueError("shift_not_found")

    st_row = await _ensure_salary_shift_state(session=session, shift=shift)

    before = {
        "state": str(getattr(st_row, "state", "")),
        "manual_hours": (str(getattr(st_row, "manual_hours", None)) if getattr(st_row, "manual_hours", None) is not None else None),
        "manual_amount_override": (
            str(getattr(st_row, "manual_amount_override", None))
            if getattr(st_row, "manual_amount_override", None) is not None
            else None
        ),
        "comment": (str(getattr(st_row, "comment", "") or "") or None),
        "is_paid": bool(getattr(st_row, "is_paid", False)),
        "updated_by_user_id": (int(getattr(st_row, "updated_by_user_id", 0) or 0) or None),
        "updated_at": (str(getattr(st_row, "updated_at", "") or "") or None),
    }

    cmt = (str(comment or "").strip() or None)
    need_comment = False
    if state != getattr(st_row, "state", SalaryShiftState.WORKED):
        need_comment = True
    if manual_hours is not None or manual_amount_override is not None:
        need_comment = True
    if state in {SalaryShiftState.OVERTIME, SalaryShiftState.SKIP, SalaryShiftState.DAY_OFF, SalaryShiftState.NEEDS_REVIEW}:
        need_comment = True
    if need_comment and not cmt:
        raise ValueError("comment_required")

    st_row.state = SalaryShiftState(str(state))
    st_row.manual_hours = q2(Decimal(manual_hours)) if manual_hours is not None else None
    st_row.manual_amount_override = q2(Decimal(manual_amount_override)) if manual_amount_override is not None else None
    st_row.comment = cmt
    st_row.updated_by_user_id = int(updated_by_user_id) if updated_by_user_id is not None else None
    session.add(st_row)
    await session.flush()

    after = {
        "state": str(getattr(st_row, "state", "")),
        "manual_hours": (str(getattr(st_row, "manual_hours", None)) if getattr(st_row, "manual_hours", None) is not None else None),
        "manual_amount_override": (
            str(getattr(st_row, "manual_amount_override", None))
            if getattr(st_row, "manual_amount_override", None) is not None
            else None
        ),
        "comment": (str(getattr(st_row, "comment", "") or "") or None),
        "is_paid": bool(getattr(st_row, "is_paid", False)),
        "updated_by_user_id": (int(getattr(st_row, "updated_by_user_id", 0) or 0) or None),
        "updated_at": (str(getattr(st_row, "updated_at", "") or "") or None),
    }
    try:
        session.add(
            SalaryShiftAudit(
                shift_id=int(shift_id),
                actor_user_id=int(updated_by_user_id) if updated_by_user_id is not None else None,
                event_type="shift_update",
                before=before,
                after=after,
                meta={
                    "day": str(getattr(shift, "day", "") or ""),
                    "notify_employee": bool(notify_employee),
                },
            )
        )
        await session.flush()
    except Exception:
        pass

    if notify_employee:
        tg_id = await load_user_tg_id(session=session, user_id=int(getattr(shift, "user_id", 0) or 0))
        if tg_id is not None:
            try:
                tot_txt = "—"
                try:
                    items = await calc_user_shifts(
                        session=session,
                        user_id=int(getattr(shift, "user_id", 0) or 0),
                        period_start=shift.day,
                        period_end=shift.day,
                    )
                    if items:
                        tot_txt = _money(items[0].total_amount)
                except Exception:
                    pass

                balance_txt = ""
                if period_start is not None and period_end is not None:
                    try:
                        totals = await calc_user_period_totals(
                            session=session,
                            user_id=int(getattr(shift, "user_id", 0) or 0),
                            period_start=period_start,
                            period_end=period_end,
                        )
                        balance_txt = f"\nБаланс за период: <b>{_esc(_money(totals.balance))}</b>"
                    except Exception:
                        balance_txt = ""

                txt_lines = [
                    "✏️ <b>Смена</b>",
                    f"{_esc(shift.day.strftime('%d.%m.%Y'))}: статус <b>{_esc(str(state))}</b>, сумма <b>{_esc(tot_txt)}</b>",
                ]
                if balance_txt:
                    try:
                        txt_lines.append(balance_txt.strip())
                    except Exception:
                        txt_lines.append(balance_txt)
                if cmt:
                    txt_lines.append(f"Комментарий: {_esc(cmt)}")
                await _tg_salary_enqueue(int(tg_id), "\n".join([x for x in txt_lines if str(x).strip()]))
            except Exception:
                pass

    return st_row


async def create_salary_adjustment(
    *,
    session: AsyncSession,
    shift_id: int,
    delta_amount: Decimal,
    comment: str,
    created_by_user_id: int | None,
    notify_employee: bool = True,
    period_start: date | None = None,
    period_end: date | None = None,
) -> SalaryAdjustment:
    cmt = str(comment or "").strip()
    if not cmt:
        raise ValueError("comment_required")

    shift = (
        await session.execute(select(ShiftInstance).where(ShiftInstance.id == int(shift_id)))
    ).scalars().first()
    if shift is None:
        raise ValueError("shift_not_found")

    adj = SalaryAdjustment(
        shift_id=int(shift_id),
        delta_amount=q2(Decimal(delta_amount)),
        comment=cmt,
        created_by_user_id=int(created_by_user_id) if created_by_user_id is not None else None,
    )
    session.add(adj)
    await session.flush()

    try:
        session.add(
            SalaryShiftAudit(
                shift_id=int(shift_id),
                actor_user_id=int(created_by_user_id) if created_by_user_id is not None else None,
                event_type="adjustment_create",
                before=None,
                after={
                    "adjustment_id": int(getattr(adj, "id", 0) or 0),
                    "delta_amount": str(getattr(adj, "delta_amount", "") or ""),
                    "comment": str(getattr(adj, "comment", "") or ""),
                    "created_at": str(getattr(adj, "created_at", "") or ""),
                },
                meta={
                    "day": str(getattr(shift, "day", "") or ""),
                    "notify_employee": bool(notify_employee),
                },
            )
        )
        await session.flush()
    except Exception:
        pass

    if notify_employee:
        tg_id = await load_user_tg_id(session=session, user_id=int(getattr(shift, "user_id", 0) or 0))
        if tg_id is not None:
            try:
                tot_txt = "—"
                try:
                    items = await calc_user_shifts(
                        session=session,
                        user_id=int(getattr(shift, "user_id", 0) or 0),
                        period_start=shift.day,
                        period_end=shift.day,
                    )
                    if items:
                        tot_txt = _money(items[0].total_amount)
                except Exception:
                    pass

                balance_txt = ""
                if period_start is not None and period_end is not None:
                    try:
                        totals = await calc_user_period_totals(
                            session=session,
                            user_id=int(getattr(shift, "user_id", 0) or 0),
                            period_start=period_start,
                            period_end=period_end,
                        )
                        balance_txt = f"\nБаланс за период: <b>{_esc(_money(totals.balance))}</b>"
                    except Exception:
                        balance_txt = ""

                txt_lines = [
                    "➕➖ <b>Корректировка</b>",
                    f"{_esc(shift.day.strftime('%d.%m.%Y'))}: изменение <b>{_esc(_money(q2(Decimal(delta_amount))))}</b>, сумма <b>{_esc(tot_txt)}</b>",
                ]
                if balance_txt:
                    try:
                        txt_lines.append(balance_txt.strip())
                    except Exception:
                        txt_lines.append(balance_txt)
                txt_lines.append(f"Комментарий: {_esc(cmt)}")
                await _tg_salary_enqueue(int(tg_id), "\n".join([x for x in txt_lines if str(x).strip()]))
            except Exception:
                pass

    return adj


async def list_salary_payouts_for_user(
    *,
    session: AsyncSession,
    user_id: int,
    limit: int = 10,
    offset: int = 0,
) -> list[SalaryPayout]:
    lim = max(1, min(100, int(limit)))
    off = max(0, int(offset))
    return list(
        (
            await session.execute(
                select(SalaryPayout)
                .where(SalaryPayout.user_id == int(user_id))
                .order_by(SalaryPayout.created_at.desc(), SalaryPayout.id.desc())
                .limit(lim)
                .offset(off)
            )
        )
        .scalars()
        .all()
    )


async def create_salary_payout(
    *,
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    period_start: date,
    period_end: date,
    comment: str | None,
    created_by_user_id: int | None,
    notify_tg_id: int | None,
) -> SalaryPayout:
    amt = q2(Decimal(amount))

    totals_before = await calc_user_period_totals(
        session=session,
        user_id=int(user_id),
        period_start=period_start,
        period_end=period_end,
    )

    p = SalaryPayout(
        user_id=int(user_id),
        amount=amt,
        period_start=period_start,
        period_end=period_end,
        comment=(str(comment).strip() or None),
        created_by_user_id=int(created_by_user_id) if created_by_user_id is not None else None,
    )
    session.add(p)
    await session.flush()

    # full payout -> mark shifts as paid
    if amt >= totals_before.balance:
        shifts = list(
            (
                await session.execute(
                    select(ShiftInstance)
                    .where(ShiftInstance.user_id == int(user_id))
                    .where(ShiftInstance.day >= period_start)
                    .where(ShiftInstance.day <= period_end)
                )
            )
            .scalars()
            .all()
        )
        for s in shifts:
            st_row = await _ensure_salary_shift_state(session=session, shift=s)
            st_row.is_paid = True
            st_row.updated_by_user_id = int(created_by_user_id) if created_by_user_id is not None else None
            session.add(st_row)

    totals_after = await calc_user_period_totals(
        session=session,
        user_id=int(user_id),
        period_start=period_start,
        period_end=period_end,
    )

    try:
        session.add(
            SalaryPayoutAudit(
                payout_id=int(getattr(p, "id", 0) or 0),
                user_id=int(user_id),
                actor_user_id=int(created_by_user_id) if created_by_user_id is not None else None,
                event_type="payout_create",
                before={
                    "accrued": str(totals_before.accrued),
                    "paid": str(totals_before.paid),
                    "balance": str(totals_before.balance),
                },
                after={
                    "payout_amount": str(amt),
                    "accrued": str(totals_after.accrued),
                    "paid": str(totals_after.paid),
                    "balance": str(totals_after.balance),
                },
                meta={
                    "period_start": str(period_start),
                    "period_end": str(period_end),
                    "comment": (str(comment).strip() or None) if comment is not None else None,
                },
            )
        )
        await session.flush()
    except Exception:
        pass

    # TG notification to employee
    if notify_tg_id is not None and int(notify_tg_id) > 0:
        try:
            period = f"{period_start.strftime('%d.%m.%Y')}–{period_end.strftime('%d.%m.%Y')}"
            cmt = (str(comment).strip() if comment is not None else "")
            txt_lines = [
                "💸 <b>Выплата</b>",
                "",
                f"Период: <b>{_esc(period)}</b>",
                f"Сумма: <b>{_esc(_money(amt))}</b>",
                f"Баланс после: <b>{_esc(_money(totals_after.balance))}</b>",
            ]
            txt_lines.append("")
            txt_lines.append(f"Комментарий: {_esc(cmt) if cmt else '—'}")
            await _tg_send_html(chat_id=int(notify_tg_id), text="\n".join(txt_lines))
        except Exception:
            pass

    return p


async def update_salary_payout(
    *,
    session: AsyncSession,
    payout_id: int,
    amount: Decimal,
    period_start: date | None,
    period_end: date | None,
    comment: str | None,
    updated_by_user_id: int | None,
) -> dict:
    p = (
        await session.execute(
            select(SalaryPayout).where(SalaryPayout.id == int(payout_id))
        )
    ).scalars().first()
    if p is None:
        raise ValueError("payout_not_found")

    old_user_id = int(getattr(p, "user_id", 0) or 0)
    old_ps = getattr(p, "period_start", None)
    old_pe = getattr(p, "period_end", None)
    old_amount = getattr(p, "amount", None)
    old_comment = getattr(p, "comment", None)

    # If period_start/period_end not provided -> keep existing
    new_ps = period_start if period_start is not None else old_ps
    new_pe = period_end if period_end is not None else old_pe
    if new_ps is None or new_pe is None:
        raise ValueError("bad_period")
    if new_ps > new_pe:
        raise ValueError("bad_period")

    new_amount = q2(Decimal(amount))
    new_comment = (str(comment).strip() or None) if comment is not None else None

    totals_before_old = await calc_user_period_totals(
        session=session,
        user_id=int(old_user_id),
        period_start=old_ps,
        period_end=old_pe,
    )

    # Apply changes
    p.amount = new_amount
    p.comment = new_comment
    p.period_start = new_ps
    p.period_end = new_pe
    session.add(p)
    await session.flush()

    # Re-evaluate "paid" flags for shifts in affected periods (old + new)
    periods = {(old_ps, old_pe), (new_ps, new_pe)}
    for ps, pe in periods:
        if ps is None or pe is None:
            continue
        totals = await calc_user_period_totals(
            session=session,
            user_id=int(old_user_id),
            period_start=ps,
            period_end=pe,
        )
        sum_paid = totals.paid
        sum_accrued = totals.accrued
        is_paid_now = bool(sum_paid >= sum_accrued)

        shifts = list(
            (
                await session.execute(
                    select(ShiftInstance)
                    .where(ShiftInstance.user_id == int(old_user_id))
                    .where(ShiftInstance.day >= ps)
                    .where(ShiftInstance.day <= pe)
                )
            )
            .scalars()
            .all()
        )
        for s in shifts:
            st_row = await _ensure_salary_shift_state(session=session, shift=s)
            st_row.is_paid = is_paid_now
            st_row.updated_by_user_id = int(updated_by_user_id) if updated_by_user_id is not None else None
            session.add(st_row)

    totals_after_new = await calc_user_period_totals(
        session=session,
        user_id=int(old_user_id),
        period_start=new_ps,
        period_end=new_pe,
    )

    before = {
        "amount": (str(old_amount) if old_amount is not None else None),
        "comment": (str(old_comment).strip() if old_comment is not None and str(old_comment).strip() else None),
        "period_start": str(old_ps) if old_ps is not None else None,
        "period_end": str(old_pe) if old_pe is not None else None,
        "paid": str(totals_before_old.paid),
        "balance": str(totals_before_old.balance),
    }
    after = {
        "amount": str(new_amount),
        "comment": (str(new_comment).strip() if new_comment is not None and str(new_comment).strip() else None),
        "period_start": str(new_ps),
        "period_end": str(new_pe),
        "paid": str(totals_after_new.paid),
        "balance": str(totals_after_new.balance),
    }
    try:
        session.add(
            SalaryPayoutAudit(
                payout_id=int(getattr(p, "id", 0) or 0),
                user_id=int(old_user_id),
                actor_user_id=int(updated_by_user_id) if updated_by_user_id is not None else None,
                event_type="payout_update",
                before=before,
                after=after,
                meta={
                    "changed": {
                        "amount": bool(str(before.get("amount") or "") != str(after.get("amount") or "")),
                        "comment": bool((before.get("comment") or None) != (after.get("comment") or None)),
                        "period": bool((before.get("period_start"), before.get("period_end")) != (after.get("period_start"), after.get("period_end"))),
                    }
                },
            )
        )
        await session.flush()
    except Exception:
        pass

    return {"before": before, "after": after}
