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


async def main() -> None:
    setup_logging(service_name="bot", log_dir="/var/log/app/bot", level=settings.LOG_LEVEL)
    logging.getLogger(__name__).info("bot starting")
    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(registration_router)
    dp.include_router(admin_router)
    dp.include_router(purchases_router)

    # Register bot commands for private and group chats
    try:
        commands = [
            BotCommand(command="start", description="Запуск бота"),
            BotCommand(command="register", description="Зарегистрироваться"),
            BotCommand(command="profile", description="Профиль"),
            BotCommand(command="purchases", description="Закупки"),
            BotCommand(command="admin", description="Админ-панель"),
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
