from __future__ import annotations

from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import ReminderSettings


class ReminderSettingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_singleton(self) -> ReminderSettings:
        res = await self.session.execute(select(ReminderSettings).where(ReminderSettings.id == 1))
        s = res.scalar_one_or_none()
        if s:
            return s
        s = ReminderSettings(id=1)
        self.session.add(s)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def set_enabled(self, enabled: bool) -> ReminderSettings:
        s = await self.get_singleton()
        s.reminders_enabled = bool(enabled)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def set_skip_weekends(self, skip_weekends: bool) -> ReminderSettings:
        s = await self.get_singleton()
        s.skip_weekends = bool(skip_weekends)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def set_send_admins(self, v: bool) -> ReminderSettings:
        s = await self.get_singleton()
        s.send_to_admins = bool(v)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def set_send_managers(self, v: bool) -> ReminderSettings:
        s = await self.get_singleton()
        s.send_to_managers = bool(v)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def set_reminder_time(self, t: time) -> ReminderSettings:
        s = await self.get_singleton()
        s.reminder_time = t
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def set_daily_report_enabled(self, v: bool) -> ReminderSettings:
        s = await self.get_singleton()
        s.daily_report_enabled = bool(v)
        await self.session.flush()
        await self.session.refresh(s)
        return s

    async def set_daily_report_time(self, t: time) -> ReminderSettings:
        s = await self.get_singleton()
        s.daily_report_time = t
        await self.session.flush()
        await self.session.refresh(s)
        return s
