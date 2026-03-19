from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import FinanceCategory, FinanceOperation, SalaryPayout, User
from shared.utils import utc_now

_Q2 = lambda v: Decimal(str(v or 0)).quantize(Decimal("0.01"))

SALARY_SOURCE_TYPE = "salary_payout"
SALARY_SYNC_PREFIX = "salary_payout:"
SALARY_CATEGORY_NAME = "Зарплаты"


def _salary_project_key(payout_id: int) -> str:
    return f"{SALARY_SYNC_PREFIX}{int(payout_id)}"


async def _ensure_salary_category(*, session: AsyncSession) -> FinanceCategory:
    cat = (
        await session.execute(
            select(FinanceCategory)
            .where(FinanceCategory.type == "expense")
            .where(FinanceCategory.name == SALARY_CATEGORY_NAME)
            .limit(1)
        )
    ).scalars().first()
    if cat is None:
        cat = FinanceCategory(type="expense", name=SALARY_CATEGORY_NAME, is_archived=False)
        session.add(cat)
        await session.flush()
    elif bool(getattr(cat, "is_archived", False)):
        cat.is_archived = False
        session.add(cat)
        await session.flush()
    return cat


async def _counterparty_for_user(*, session: AsyncSession, user_id: int) -> str:
    user = (
        await session.execute(select(User).where(User.id == int(user_id)))
    ).scalars().first()
    if user is None:
        return f"#{int(user_id)}" if int(user_id) > 0 else "Сотрудник"
    full_name = " ".join(
        [
            str(getattr(user, "first_name", "") or "").strip(),
            str(getattr(user, "last_name", "") or "").strip(),
        ]
    ).strip()
    return full_name or (f"#{int(user_id)}" if int(user_id) > 0 else "Сотрудник")


async def sync_salary_payout_operation(
    *,
    session: AsyncSession,
    payout_id: int,
    user_id: int,
    amount: Decimal,
    occurred_at: datetime,
    comment: str | None,
    created_by_user_id: int | None,
) -> FinanceOperation:
    pid = int(payout_id)
    key = _salary_project_key(pid)

    rows = list(
        (
            await session.execute(
                select(FinanceOperation)
                .where(
                    or_(
                        (FinanceOperation.source_type == SALARY_SOURCE_TYPE)
                        & (FinanceOperation.source_id == pid),
                        FinanceOperation.project == key,
                    )
                )
                .order_by(FinanceOperation.id.asc())
            )
        )
        .scalars()
        .all()
    )
    op = rows[0] if rows else None
    for dup in rows[1:]:
        await session.delete(dup)

    cat = await _ensure_salary_category(session=session)
    counterparty = await _counterparty_for_user(session=session, user_id=int(user_id))
    amount_v = _Q2(amount)
    if amount_v <= 0:
        amount_v = _Q2(abs(amount_v))
    comment_v = (str(comment or "").strip() or "—")

    if op is None:
        op = FinanceOperation(
            type="expense",
            amount=amount_v,
            occurred_at=occurred_at,
            category_id=int(cat.id),
            subcategory=None,
            project=key,
            source_type=SALARY_SOURCE_TYPE,
            source_id=pid,
            counterparty=counterparty,
            payment_method=None,
            comment=comment_v,
            created_by_user_id=created_by_user_id,
        )
    else:
        op.type = "expense"
        op.amount = amount_v
        op.occurred_at = occurred_at
        op.category_id = int(cat.id)
        op.project = key
        op.source_type = SALARY_SOURCE_TYPE
        op.source_id = pid
        op.counterparty = counterparty
        op.comment = comment_v
        if created_by_user_id is not None:
            op.created_by_user_id = created_by_user_id

    session.add(op)
    await session.flush()
    return op


async def remove_salary_payout_operation(*, session: AsyncSession, payout_id: int) -> int:
    pid = int(payout_id)
    key = _salary_project_key(pid)
    res = await session.execute(
        delete(FinanceOperation).where(
            or_(
                (FinanceOperation.source_type == SALARY_SOURCE_TYPE)
                & (FinanceOperation.source_id == pid),
                FinanceOperation.project == key,
            )
        )
    )
    await session.flush()
    return int(res.rowcount or 0)


async def backfill_salary_payout_operations(*, session: AsyncSession) -> dict:
    payouts = list(
        (
            await session.execute(
                select(SalaryPayout).order_by(SalaryPayout.id.asc())
            )
        )
        .scalars()
        .all()
    )

    processed = 0
    for p in payouts:
        await sync_salary_payout_operation(
            session=session,
            payout_id=int(getattr(p, "id", 0) or 0),
            user_id=int(getattr(p, "user_id", 0) or 0),
            amount=_Q2(getattr(p, "amount", 0) or 0),
            occurred_at=(getattr(p, "created_at", None) or utc_now()),
            comment=(str(getattr(p, "comment", "") or "").strip() or None),
            created_by_user_id=(int(getattr(p, "created_by_user_id", 0) or 0) or None),
        )
        processed += 1

    return {"processed": processed}
