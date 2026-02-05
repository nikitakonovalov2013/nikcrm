from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import CommandStart, Command
from aiogram.utils.markdown import hbold
import logging

from shared.db import get_async_session
from shared.enums import UserStatus, Schedule, Position
from shared.models import User
from bot.app.states.registration import RegistrationState
from bot.app.keyboards.main import main_menu_kb
from bot.app.keyboards.inline import schedule_kb, position_kb
from bot.app.utils.parsing import parse_birth_date
from bot.app.repository.users import UserRepository, UserAlreadyRegisteredError
from shared.utils import format_date
from bot.app.utils.bot_commands import sync_commands_for_chat
from bot.app.utils.urls import _public_base_url
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    logging.getLogger(__name__).info("/start received", extra={"tg_id": message.from_user.id})
    async with get_async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_tg_id(message.from_user.id)
    is_admin = False
    from shared.config import settings

    try:
        is_admin = message.from_user.id in settings.admin_ids
    except Exception:
        is_admin = False

    await message.answer(
        "👋 Добро пожаловать в бот!\n\nВыберите действие ниже.",
        reply_markup=main_menu_kb(user.status if user else None, message.from_user.id, user.position if user else None),
    )

    try:
        await sync_commands_for_chat(
            bot=message.bot,
            chat_id=message.chat.id,
            is_admin=message.from_user.id in settings.admin_ids,
            status=user.status if user else None,
            position=user.position if user else None,
        )
    except Exception:
        pass


@router.message(F.text.in_({"Зарегистрироваться", "📝 Зарегистрироваться"}))
@router.message(Command("register"))
async def start_registration(message: Message, state: FSMContext):
    logging.getLogger(__name__).info("registration start", extra={"tg_id": message.from_user.id})
    async with get_async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_tg_id(message.from_user.id)
    if user:
        if user.status == UserStatus.BLACKLISTED:
            await message.answer(
                "🚫 Доступ ограничен. Вы не можете использовать бота.\n\nЕсли вы считаете, что это ошибка — свяжитесь с администратором.",
            )
            return
        if user.status in (UserStatus.PENDING, UserStatus.APPROVED, UserStatus.REJECTED):
            await message.answer(
                "ℹ️ Вы уже подавали заявку или зарегистрированы. Откройте \"Профиль\" в меню ниже.",
            )
            return
    await state.clear()
    await state.set_state(RegistrationState.first_name)
    await message.answer("📝 Укажите имя.\n\nНапример: Иван")


@router.message(RegistrationState.first_name)
async def reg_first_name(message: Message, state: FSMContext):
    logging.getLogger(__name__).debug("step first_name", extra={"tg_id": message.from_user.id, "value": message.text.strip()})
    await state.update_data(first_name=message.text.strip())
    await state.set_state(RegistrationState.last_name)
    await message.answer("📝 Укажите фамилию.\n\nНапример: Петров")


@router.message(RegistrationState.last_name)
async def reg_last_name(message: Message, state: FSMContext):
    logging.getLogger(__name__).debug("step last_name", extra={"tg_id": message.from_user.id, "value": message.text.strip()})
    await state.update_data(last_name=message.text.strip())
    await state.set_state(RegistrationState.birth_date)
    await message.answer("📅 Введите дату рождения в формате ДД.ММ.ГГГГ.\n\nНапример: 21.04.1995")


@router.message(RegistrationState.birth_date)
async def reg_birth_date(message: Message, state: FSMContext):
    d = parse_birth_date(message.text)
    if not d:
        logging.getLogger(__name__).warning("invalid birth_date format", extra={"tg_id": message.from_user.id, "raw": message.text})
        await message.answer("❌ Неверный формат даты.\n\nПожалуйста, введите дату в формате ДД.ММ.ГГГГ.\nНапример: 21.04.1995")
        return
    logging.getLogger(__name__).debug("step birth_date", extra={"tg_id": message.from_user.id, "value": str(d)})
    await state.update_data(birth_date=d)
    await state.set_state(RegistrationState.rate_k)
    await message.answer("💰 Введите ставку, ₽ (только число).\n\nНапример: 120")


@router.message(RegistrationState.rate_k)
async def reg_rate(message: Message, state: FSMContext):
    try:
        rate = int(message.text.strip())
    except ValueError:
        logging.getLogger(__name__).warning("invalid rate", extra={"tg_id": message.from_user.id, "raw": message.text})
        await message.answer("❌ Пожалуйста, введите целое число без букв и дополнительных символов.")
        return
    logging.getLogger(__name__).debug("step rate_k", extra={"tg_id": message.from_user.id, "value": rate})
    await state.update_data(rate_k=rate)
    await state.set_state(RegistrationState.schedule)
    await message.answer("🗓️ Выберите график работы:", reply_markup=schedule_kb())


@router.callback_query(RegistrationState.schedule, F.data.startswith("schedule:"))
async def reg_schedule_cb(cb: CallbackQuery, state: FSMContext):
    _, val = cb.data.split(":", 1)
    if val not in {s.value for s in Schedule}:
        logging.getLogger(__name__).warning("invalid schedule", extra={"tg_id": cb.from_user.id, "raw": val})
        await cb.answer("❌ Неверный график. Пожалуйста, выберите один из предложенных вариантов.", show_alert=True)
        return
    logging.getLogger(__name__).debug("step schedule", extra={"tg_id": cb.from_user.id, "value": val})
    await state.update_data(schedule=Schedule(val))
    await state.set_state(RegistrationState.position)
    try:
        await cb.message.edit_text("👔 Выберите должность:", reply_markup=position_kb())
    except Exception:
        await cb.message.answer("👔 Выберите должность:", reply_markup=position_kb())
    await cb.answer()


@router.callback_query(RegistrationState.position, F.data.startswith("position:"))
async def reg_position_cb(cb: CallbackQuery, state: FSMContext):
    _, val = cb.data.split(":", 1)
    if val not in {p.value for p in Position}:
        logging.getLogger(__name__).warning("invalid position", extra={"tg_id": cb.from_user.id, "raw": val})
        await cb.answer("❌ Неверная должность. Пожалуйста, выберите один из предложенных вариантов.", show_alert=True)
        return
    logging.getLogger(__name__).debug("step position", extra={"tg_id": cb.from_user.id, "value": val})
    await state.update_data(position=Position(val))

    data = await state.get_data()
    async with get_async_session() as session:
        repo = UserRepository(session)
        exists = await repo.get_by_tg_id(cb.from_user.id)
        if exists and exists.status == UserStatus.BLACKLISTED:
            await cb.message.answer("Вы в черном списке и не можете зарегистрироваться.")
            await state.clear()
            await cb.answer()
            return
        try:
            user = await repo.create_pending(
                tg_id=cb.from_user.id,
                first_name=data["first_name"],
                last_name=data["last_name"],
                birth_date=data["birth_date"],
                rate_k=data["rate_k"],
                schedule=data["schedule"],
                position=data["position"],
            )
        except UserAlreadyRegisteredError as e:
            u = e.user
            await state.clear()

            if u.status == UserStatus.BLACKLISTED:
                await cb.message.answer(
                    "🚫 Доступ ограничен. Вы не можете использовать бота.\n\nЕсли вы считаете, что это ошибка — свяжитесь с администратором.",
                    reply_markup=main_menu_kb(None, cb.from_user.id),
                )
                await cb.answer()
                return

            if u.status == UserStatus.APPROVED:
                await cb.message.answer(
                    "ℹ️ Вы уже зарегистрированы. Откройте \"Профиль\" в меню ниже.",
                    reply_markup=main_menu_kb(u.status, cb.from_user.id, u.position),
                )
                await cb.answer()
                return

            await cb.message.answer(
                "⏳ Ваша заявка уже отправлена и находится на рассмотрении.\n\nОткройте \"Профиль\" в меню ниже.",
                reply_markup=main_menu_kb(u.status, cb.from_user.id, u.position),
            )
            await cb.answer()
            return

    await state.clear()
    try:
        from shared.config import settings

        await sync_commands_for_chat(
            bot=cb.bot,
            chat_id=cb.message.chat.id,
            is_admin=cb.from_user.id in settings.admin_ids,
            status=user.status,
            position=user.position,
        )
    except Exception:
        pass
    try:
        await cb.message.edit_text("✅ Заявка отправлена на рассмотрение администратору.\n\nМы уведомим вас, как только статус изменится.")
    except Exception:
        await cb.message.answer(
            "✅ Заявка отправлена на рассмотрение администратору.\n\nМы уведомим вас, как только статус изменится.",
            reply_markup=main_menu_kb(user.status, cb.from_user.id, user.position),
        )
    else:
        # If edit_text succeeded (no keyboard possible), send a follow-up message to update the keyboard
        await cb.message.answer(
            "ℹ️ Меню обновлено в соответствии со статусом заявки.",
            reply_markup=main_menu_kb(user.status, cb.from_user.id, user.position),
        )
    logging.getLogger(__name__).info("registration saved and sent to admins", extra={"tg_id": cb.from_user.id, "user_id": user.id})
    await cb.answer()

    # notify admins
    from shared.config import settings
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from bot.app.keyboards.inline import approve_reject_kb

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    bd = format_date(user.birth_date)
    rate = f"{user.rate_k} ₽" if user.rate_k is not None else ''
    text = (
        "🆕 Новая заявка на регистрацию\n\n"
        f"👤 TG: {user.tg_id}\n"
        f"🧾 Имя: {user.first_name}\n"
        f"🧾 Фамилия: {user.last_name}\n"
        f"📅 Дата рождения: {bd}\n"
        f"💰 Ставка: {rate}\n"
        f"🗓️ График: {user.schedule}\n"
        f"👔 Должность: {user.position}"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=text, reply_markup=approve_reject_kb(user.id))
            logging.getLogger(__name__).debug("sent application to admin", extra={"tg_id": cb.from_user.id, "admin_tg_id": admin_id})
        except Exception:
            logging.getLogger(__name__).exception("failed to notify admin", extra={"tg_id": cb.from_user.id, "admin_tg_id": admin_id})
    await bot.session.close()


@router.message(F.text.in_({"Профиль", "🧾 Профиль"}))
@router.message(Command("profile"))
async def profile(message: Message):
    logging.getLogger(__name__).info("profile requested", extra={"tg_id": message.from_user.id})
    async with get_async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_tg_id(message.from_user.id)
    if not user:
        await message.answer(
            "ℹ️ Вы не зарегистрированы.\n\nНажмите кнопку \"Зарегистрироваться\" ниже, чтобы начать.",
            reply_markup=main_menu_kb(None, message.from_user.id),
        )
        return
    if user.status == UserStatus.BLACKLISTED:
        # Вести себя как для незарегистрированного: показать кнопку "Зарегистрироваться"
        await message.answer(
            "🚫 Доступ ограничен.\n\nЕсли вы считаете, что это ошибка — свяжитесь с администратором.\n\nНажмите кнопку \"Зарегистрироваться\" ниже, чтобы начать заново.",
            reply_markup=main_menu_kb(None, message.from_user.id),
        )
        return
    if user.status != UserStatus.APPROVED:
        await message.answer(
            "⏳ Ваша заявка находится на рассмотрении.\n\nМы сообщим вам, как только она будет обработана.",
            reply_markup=main_menu_kb(user.status, message.from_user.id, user.position),
        )
        return
    bd = format_date(user.birth_date)
    rate = f"{user.rate_k} ₽" if user.rate_k is not None else ''
    status_map = {
        UserStatus.PENDING: 'На рассмотрении',
        UserStatus.APPROVED: 'Одобрен',
        UserStatus.REJECTED: 'Отклонён',
        UserStatus.BLACKLISTED: 'В чёрном списке',
    }
    status_ru = status_map.get(user.status, '')
    text = (
        "🧾 Ваш профиль\n\n"
        f"👤 Имя: {user.first_name}\n"
        f"👤 Фамилия: {user.last_name}\n"
        f"📅 Дата рождения: {bd}\n"
        f"💰 Ставка: {rate}\n"
        f"🗓️ График: {user.schedule}\n"
        f"👔 Должность: {user.position}\n"
        f"🟢 Статус: {status_ru}"
    )

    kb = None
    try:
        base = _public_base_url()
        url = (base + "/about") if base else "/about"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="О нас", url=str(url))]]
        )
    except Exception:
        kb = None

    await message.answer(text, reply_markup=kb)

    try:
        from shared.config import settings

        await sync_commands_for_chat(
            bot=message.bot,
            chat_id=message.chat.id,
            is_admin=message.from_user.id in settings.admin_ids,
            status=user.status,
            position=user.position,
        )
    except Exception:
        pass
