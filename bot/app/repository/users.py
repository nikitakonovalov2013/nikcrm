from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from shared.models import User
from shared.enums import UserStatus, Schedule, Position
from datetime import date
from shared.utils import utc_now
from shared.services.user_color import assign_user_color


class UserAlreadyRegisteredError(Exception):
    def __init__(self, user: User):
        super().__init__("User already exists and is not deleted")
        self.user = user


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_tg_id(self, tg_id: int) -> User | None:
        res = await self.session.execute(select(User).where(User.tg_id == tg_id).where(User.is_deleted == False))
        return res.scalar_one_or_none()

    async def get_by_tg_id_any(self, tg_id: int) -> User | None:
        res = await self.session.execute(select(User).where(User.tg_id == tg_id))
        return res.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        res = await self.session.execute(select(User).where(User.id == user_id).where(User.is_deleted == False))
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
        q = select(User).where(User.tg_id == tg_id).with_for_update()
        existing = (await self.session.execute(q)).scalar_one_or_none()

        if existing is not None:
            if existing.is_deleted:
                existing.is_deleted = False
                existing.status = UserStatus.PENDING
                existing.first_name = first_name
                existing.last_name = last_name
                existing.birth_date = birth_date
                existing.rate_k = rate_k
                existing.schedule = schedule
                existing.position = position
                if not getattr(existing, "color", None):
                    existing.color = await assign_user_color(self.session, seed=int(tg_id))
                existing.updated_at = utc_now()
                await self.session.flush()
                await self.session.refresh(existing)
                return existing

            raise UserAlreadyRegisteredError(existing)

        user = User(
            tg_id=tg_id,
            first_name=first_name,
            last_name=last_name,
            birth_date=birth_date,
            rate_k=rate_k,
            schedule=schedule,
            position=position,
            status=UserStatus.PENDING,
            color=await assign_user_color(self.session, seed=int(tg_id)),
        )
        self.session.add(user)
        try:
            await self.session.flush()
            await self.session.refresh(user)
            return user
        except IntegrityError:
            # Another concurrent request may have inserted the same tg_id.
            await self.session.rollback()

            q2 = select(User).where(User.tg_id == tg_id).with_for_update()
            existing2 = (await self.session.execute(q2)).scalar_one_or_none()
            if existing2 is None:
                raise

            if existing2.is_deleted:
                existing2.is_deleted = False
                existing2.status = UserStatus.PENDING
                existing2.first_name = first_name
                existing2.last_name = last_name
                existing2.birth_date = birth_date
                existing2.rate_k = rate_k
                existing2.schedule = schedule
                existing2.position = position
                if not getattr(existing2, "color", None):
                    existing2.color = await assign_user_color(self.session, seed=int(tg_id))
                existing2.updated_at = utc_now()
                await self.session.flush()
                await self.session.refresh(existing2)
                return existing2

            raise UserAlreadyRegisteredError(existing2)

    async def update_status(self, user: User, status: UserStatus) -> User:
        user.status = status
        await self.session.flush()
        await self.session.refresh(user)
        return user
