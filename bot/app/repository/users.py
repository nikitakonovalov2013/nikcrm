from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from shared.models import User
from shared.enums import UserStatus, Schedule, Position
from datetime import date


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_tg_id(self, tg_id: int) -> User | None:
        res = await self.session.execute(select(User).where(User.tg_id == tg_id))
        return res.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        res = await self.session.execute(select(User).where(User.id == user_id))
        return res.scalar_one_or_none()

    async def create_pending(
        self,
        tg_id: int,
        first_name: str,
        last_name: str,
        birth_date: date,
        rate_k: int,
        schedule: Schedule,
        position: Position,
    ) -> User:
        user = User(
            tg_id=tg_id,
            first_name=first_name,
            last_name=last_name,
            birth_date=birth_date,
            rate_k=rate_k,
            schedule=schedule,
            position=position,
        )
        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def update_status(self, user: User, status: UserStatus) -> User:
        user.status = status
        await self.session.flush()
        await self.session.refresh(user)
        return user
