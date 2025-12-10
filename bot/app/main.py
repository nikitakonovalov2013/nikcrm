import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from shared.config import settings
from shared.logging import setup_logging
from bot.app.handlers.registration import router as registration_router
from bot.app.handlers.admin import router as admin_router


async def main() -> None:
    setup_logging(service_name="bot", log_dir="/var/log/app/bot", level=settings.LOG_LEVEL)
    logging.getLogger(__name__).info("bot starting")
    bot = Bot(token=settings.BOT_TOKEN, parse_mode="HTML")
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(registration_router)
    dp.include_router(admin_router)

    try:
        await dp.start_polling(bot)
    finally:
        logging.getLogger(__name__).info("bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
