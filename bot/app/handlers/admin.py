from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from shared.db import get_async_session
from shared.enums import UserStatus, AdminActionType, Position
from bot.app.repository.users import UserRepository
from bot.app.repository.admin_actions import AdminActionRepository
from bot.app.services.jwt_links import create_admin_jwt, create_manager_jwt
from shared.config import settings
import logging
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from bot.app.utils.bot_commands import sync_commands_for_chat
from shared.permissions import role_flags
from bot.app.utils.access import is_admin_or_manager

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


async def send_admin_panel_link(message: Message) -> None:
    async with get_async_session() as session:
        urepo = UserRepository(session)
        user = await urepo.get_by_tg_id(message.from_user.id)

    can = False
    token = None
    if is_admin(message.from_user.id):
        can = True
        token = create_admin_jwt(message.from_user.id)
    elif user and user.status == UserStatus.APPROVED and user.position == Position.MANAGER:
        can = True
        token = create_manager_jwt(message.from_user.id)

    if not can or not token:
        return
    logging.getLogger(__name__).info("admin requested admin panel", extra={"tg_id": message.from_user.id})
    base = settings.admin_panel_url.rstrip("/")
    url = f"{base}/auth?token={token}"
    await message.answer(f"Ссылка на панель администратора:\n{url}")


@router.message(F.text.in_({"🛠 Админ-панель"}))
@router.message(Command("admin"))
async def admin_panel_entry(message: Message):
    await send_admin_panel_link(message)


@router.message(F.text.in_({"Сотрудники", "👥 Сотрудники"}))
@router.message(Command("staff"))
async def employees_link(message: Message):
    await send_admin_panel_link(message)


@router.callback_query(F.data.startswith("approve:"))
async def cb_approve(call: CallbackQuery):
    r = role_flags(tg_id=int(call.from_user.id), admin_ids=settings.admin_ids, status=None, position=None)
    if not is_admin_or_manager(r=r):
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
        try:
            await sync_commands_for_chat(
                bot=bot,
                chat_id=int(user.tg_id),
                is_admin=bool(role_flags(tg_id=int(user.tg_id), admin_ids=settings.admin_ids, status=user.status, position=user.position).is_admin
                            or role_flags(tg_id=int(user.tg_id), admin_ids=settings.admin_ids, status=user.status, position=user.position).is_manager),
                status=user.status,
                position=user.position,
            )
        except Exception:
            pass
        await bot.session.close()
    except Exception:
        pass
    await call.message.edit_text(call.message.text + "\n\n✅ Подтвержден")
    await call.answer("Подтвержден")


@router.callback_query(F.data.startswith("reject:"))
async def cb_reject(call: CallbackQuery):
    r = role_flags(tg_id=int(call.from_user.id), admin_ids=settings.admin_ids, status=None, position=None)
    if not is_admin_or_manager(r=r):
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
        try:
            await sync_commands_for_chat(
                bot=bot,
                chat_id=int(user.tg_id),
                is_admin=bool(role_flags(tg_id=int(user.tg_id), admin_ids=settings.admin_ids, status=user.status, position=user.position).is_admin
                            or role_flags(tg_id=int(user.tg_id), admin_ids=settings.admin_ids, status=user.status, position=user.position).is_manager),
                status=user.status,
                position=user.position,
            )
        except Exception:
            pass
        await bot.session.close()
    except Exception:
        pass
    await call.message.edit_text(call.message.text + "\n\n❌ Отклонен (в черном списке)")
    await call.answer("Отклонен")
