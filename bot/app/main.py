import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeAllGroupChats
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from shared.config import settings
from shared.logging import setup_logging
from bot.app.handlers.registration import router as registration_router
from bot.app.handlers.admin import router as admin_router
from bot.app.handlers.purchases import router as purchases_router
from bot.app.handlers.stocks import router as stocks_router
from bot.app.handlers.reports_reminders import router as reports_reminders_router
from bot.app.services.reminders_scheduler import start_scheduler, reschedule_from_db


async def main() -> None:
    setup_logging(service_name="bot", log_dir="/var/log/app/bot", level=settings.LOG_LEVEL)
    logging.getLogger(__name__).info("bot starting")
    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(registration_router)
    dp.include_router(admin_router)
    dp.include_router(purchases_router)
    dp.include_router(stocks_router)
    dp.include_router(reports_reminders_router)

    try:
        logging.getLogger(__name__).info("starting reminders scheduler")
        start_scheduler()
        logging.getLogger(__name__).info("reminders scheduler start requested")
    except Exception:
        logging.getLogger(__name__).exception("failed to start reminders scheduler")

    try:
        logging.getLogger(__name__).info("rescheduling reminders jobs from db")
        await reschedule_from_db()
        logging.getLogger(__name__).info("reminders jobs rescheduled from db")
    except Exception:
        logging.getLogger(__name__).exception("failed to reschedule reminders jobs from db")

    # Register bot commands for private and group chats
    try:
        # Keep global commands minimal; role-specific commands are synced per-user (BotCommandScopeChat).
        commands = [
            BotCommand(command="start", description="Запуск бота"),
            BotCommand(command="profile", description="Профиль"),
        ]
        await bot.set_my_commands(commands=commands, scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands(commands=commands, scope=BotCommandScopeAllGroupChats())
    except Exception:
        # Do not fail startup if commands setup fails
        pass

    try:
        await dp.start_polling(bot)
    finally:
        logging.getLogger(__name__).info("bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
