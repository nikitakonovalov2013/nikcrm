from sqlalchemy.ext.asyncio import AsyncSession
from shared.models import AdminAction
from shared.enums import AdminActionType


class AdminActionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log(self, admin_tg_id: int, user_id: int, action: AdminActionType, payload: dict | None = None):
        rec = AdminAction(admin_tg_id=admin_tg_id, user_id=user_id, action=action, payload=payload)
        self.session.add(rec)
        await self.session.flush()
        return rec
