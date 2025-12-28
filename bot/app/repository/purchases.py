from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from shared.models import Purchase
from shared.enums import PurchaseStatus


class PurchaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, user_id: int, text: str, photo_file_id: str | None = None) -> Purchase:
        p = Purchase(user_id=user_id, text=text, photo_file_id=photo_file_id)
        self.session.add(p)
        await self.session.flush()
        await self.session.refresh(p)
        return p

    async def get_by_id(self, purchase_id: int) -> Purchase | None:
        res = await self.session.execute(select(Purchase).where(Purchase.id == purchase_id))
        return res.scalar_one_or_none()

    async def update_status(self, purchase: Purchase, status: PurchaseStatus) -> Purchase:
        purchase.status = status
        await self.session.flush()
        await self.session.refresh(purchase)
        return purchase
