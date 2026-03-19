"""Finance Telegram bot entry point."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

from shared.config import settings
from shared.logging import setup_logging
from finance_bot.app.handlers.operations import router as ops_router, ALLOWED_TG_IDS


async def main() -> None:
    setup_logging(service_name="finance_bot", log_dir="/var/log/app/finance_bot", level=settings.LOG_LEVEL)
    log = logging.getLogger(__name__)
    log.info("finance_bot starting")

    token = str(getattr(settings, "FINANCE_BOT_TOKEN", "") or "").strip()
    if not token:
        log.error("FINANCE_BOT_TOKEN is not configured — finance_bot cannot start")
        return

    allowed_raw = str(getattr(settings, "admin_ids", "") or "").strip()
    if allowed_raw:
        for part in str(allowed_raw).replace(",", " ").split():
            try:
                ALLOWED_TG_IDS.add(int(part))
            except ValueError:
                pass
    log.info("finance_bot allowed tg_ids: %s", ALLOWED_TG_IDS or "all (open)")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(ops_router)

    try:
        await bot.set_my_commands(
            commands=[
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="menu", description="Главное меню"),
            ],
            scope=BotCommandScopeAllPrivateChats(),
        )
    except Exception:
        log.warning("failed to set bot commands", exc_info=True)

    try:
        await dp.start_polling(bot)
    finally:
        log.info("finance_bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
