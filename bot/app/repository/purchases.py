from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from shared.models import Purchase, PurchaseEvent
from shared.enums import PurchaseStatus


class PurchaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        text: str,
        photo_file_id: str | None = None,
        *,
        tg_photo_file_id: str | None = None,
        photo_key: str | None = None,
        photo_path: str | None = None,
        photo_url: str | None = None,
        priority: str | None = None,
    ) -> Purchase:
        p = Purchase(
            user_id=user_id,
            text=text,
            photo_file_id=photo_file_id,
            tg_photo_file_id=tg_photo_file_id,
            photo_key=photo_key,
            photo_path=photo_path,
            photo_url=photo_url,
            priority=(str(priority).strip() if priority is not None and str(priority).strip() else None),
        )
        self.session.add(p)
        await self.session.flush()
        await self.session.refresh(p)
        return p

    async def get_by_id(self, purchase_id: int) -> Purchase | None:
        res = await self.session.execute(select(Purchase).where(Purchase.id == purchase_id))
        return res.scalar_one_or_none()

    async def get_by_id_full(self, purchase_id: int) -> Purchase | None:
        res = await self.session.execute(
            select(Purchase)
            .where(Purchase.id == int(purchase_id))
            .options(selectinload(Purchase.events).selectinload(PurchaseEvent.actor_user))
        )
        return res.scalar_one_or_none()

    async def update_status(self, purchase: Purchase, status: PurchaseStatus) -> Purchase:
        purchase.status = status
        await self.session.flush()
        await self.session.refresh(purchase)
        return purchase

    async def update_tg_message_link(self, *, purchase_id: int, tg_chat_id: int | None, tg_message_id: int | None) -> None:
        res = await self.session.execute(select(Purchase).where(Purchase.id == int(purchase_id)))
        p = res.scalar_one_or_none()
        if not p:
            return
        try:
            p.tg_chat_id = int(tg_chat_id) if tg_chat_id is not None else None
        except Exception:
            p.tg_chat_id = None
        try:
            p.tg_message_id = int(tg_message_id) if tg_message_id is not None else None
        except Exception:
            p.tg_message_id = None
        await self.session.flush()
