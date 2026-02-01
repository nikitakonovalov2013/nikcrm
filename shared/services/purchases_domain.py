from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enums import PurchaseStatus
from shared.models import Purchase
from shared.utils import utc_now


@dataclass(frozen=True)
class PurchaseTransitionResult:
    purchase_id: int
    status: PurchaseStatus
    changed: bool


async def load_purchase_for_update(session: AsyncSession, purchase_id: int) -> Purchase:
    res = await session.execute(
        select(Purchase)
        .where(Purchase.id == int(purchase_id))
        .with_for_update()
        .options(
            selectinload(Purchase.user),
            selectinload(Purchase.taken_by_user),
            selectinload(Purchase.bought_by_user),
            selectinload(Purchase.archived_by_user),
        )
    )
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Закупка не найдена")
    return p


def _status_value(st) -> str:
    return st.value if hasattr(st, "value") else str(st or "")


async def purchase_take_in_work(*, session: AsyncSession, purchase_id: int, actor_user_id: int) -> PurchaseTransitionResult:
    p = await load_purchase_for_update(session, int(purchase_id))
    st = _status_value(getattr(p, "status", None))
    if st == PurchaseStatus.IN_PROGRESS.value:
        return PurchaseTransitionResult(purchase_id=int(p.id), status=PurchaseStatus.IN_PROGRESS, changed=False)
    if st != PurchaseStatus.NEW.value:
        raise HTTPException(status_code=400, detail="Нельзя взять в работу")

    if getattr(p, "taken_by_user_id", None) is not None:
        raise HTTPException(status_code=409, detail="Уже взято в работу")

    p.taken_by_user_id = int(actor_user_id)
    try:
        p.taken_at = utc_now()
    except Exception:
        pass
    p.status = PurchaseStatus.IN_PROGRESS
    await session.flush()
    return PurchaseTransitionResult(purchase_id=int(p.id), status=PurchaseStatus.IN_PROGRESS, changed=True)


async def purchase_cancel(*, session: AsyncSession, purchase_id: int, actor_user_id: int) -> PurchaseTransitionResult:
    p = await load_purchase_for_update(session, int(purchase_id))
    st = _status_value(getattr(p, "status", None))
    if st == PurchaseStatus.CANCELED.value:
        return PurchaseTransitionResult(purchase_id=int(p.id), status=PurchaseStatus.CANCELED, changed=False)
    if st == PurchaseStatus.BOUGHT.value:
        raise HTTPException(status_code=409, detail="Уже закрыто")
    if st not in {PurchaseStatus.NEW.value, PurchaseStatus.IN_PROGRESS.value}:
        raise HTTPException(status_code=400, detail="Нельзя отменить")

    p.status = PurchaseStatus.CANCELED
    try:
        p.archived_at = utc_now()
        p.archived_by_user_id = int(actor_user_id)
    except Exception:
        pass
    await session.flush()
    return PurchaseTransitionResult(purchase_id=int(p.id), status=PurchaseStatus.CANCELED, changed=True)


async def purchase_mark_bought(*, session: AsyncSession, purchase_id: int, actor_user_id: int) -> PurchaseTransitionResult:
    p = await load_purchase_for_update(session, int(purchase_id))
    st = _status_value(getattr(p, "status", None))
    if st == PurchaseStatus.BOUGHT.value:
        return PurchaseTransitionResult(purchase_id=int(p.id), status=PurchaseStatus.BOUGHT, changed=False)
    if st != PurchaseStatus.IN_PROGRESS.value:
        raise HTTPException(status_code=400, detail="Куплено можно поставить только для закупок в работе")

    p.bought_by_user_id = int(actor_user_id)
    p.bought_at = utc_now()
    p.status = PurchaseStatus.BOUGHT
    try:
        p.archived_at = utc_now()
        p.archived_by_user_id = int(actor_user_id)
    except Exception:
        pass
    await session.flush()
    return PurchaseTransitionResult(purchase_id=int(p.id), status=PurchaseStatus.BOUGHT, changed=True)
