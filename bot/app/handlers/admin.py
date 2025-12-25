from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from shared.db import get_async_session
from shared.enums import UserStatus, AdminActionType
from bot.app.repository.users import UserRepository
from bot.app.repository.admin_actions import AdminActionRepository
from bot.app.services.jwt_links import create_admin_jwt
from shared.config import settings
import logging
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.message(F.text.in_({"Сотрудники", "👥 Сотрудники"}))
@router.message(Command("staff"))
async def employees_link(message: Message):
    if not is_admin(message.from_user.id):
        return
    logging.getLogger(__name__).info("admin requested employees link", extra={"tg_id": message.from_user.id})
    token = create_admin_jwt(message.from_user.id)
    # Use public admin panel URL that already includes /crm prefix
    base = settings.admin_panel_url.rstrip("/")
    url = f"{base}/auth?token={token}"
    await message.answer(f"Ссылка на панель администратора:\n{url}")


@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Недостаточно прав", show_alert=True)
        return
    user_id = int(call.data.split(":", 1)[1])
    logging.getLogger(__name__).info("approve clicked", extra={"tg_id": call.from_user.id, "user_id": user_id})
    async with get_async_session() as session:
        urepo = UserRepository(session)
        arepo = AdminActionRepository(session)
        user = await urepo.get_by_id(user_id)
        if not user:
            await call.answer("Пользователь не найден", show_alert=True)
            return
        await urepo.update_status(user, UserStatus.APPROVED)
        await arepo.log(call.from_user.id, user.id, AdminActionType.APPROVE, None)
    # notify user
    try:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        await bot.send_message(user.tg_id, "Ваша заявка подтверждена. Доступен профиль в боте.")
        await bot.session.close()
    except Exception:
        pass
    await call.message.edit_text(call.message.text + "\n\n✅ Подтвержден")
    await call.answer("Подтвержден")


@router.callback_query(F.data.startswith("reject:"))
async def cb_reject(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Недостаточно прав", show_alert=True)
        return
    user_id = int(call.data.split(":", 1)[1])
    logging.getLogger(__name__).info("reject clicked", extra={"tg_id": call.from_user.id, "user_id": user_id})
    async with get_async_session() as session:
        urepo = UserRepository(session)
        arepo = AdminActionRepository(session)
        user = await urepo.get_by_id(user_id)
        if not user:
            await call.answer("Пользователь не найден", show_alert=True)
            return
        await urepo.update_status(user, UserStatus.BLACKLISTED)
        await arepo.log(call.from_user.id, user.id, AdminActionType.REJECT, None)
    # notify user
    try:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        await bot.send_message(user.tg_id, "Ваша заявка отклонена. Дальнейшее использование бота ограничено.")
        await bot.session.close()
    except Exception:
        pass
    await call.message.edit_text(call.message.text + "\n\n❌ Отклонен (в черном списке)")
    await call.answer("Отклонен")
