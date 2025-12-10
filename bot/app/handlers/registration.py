from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.filters import CommandStart
from aiogram.utils.markdown import hbold
import logging

from shared.db import get_async_session
from shared.enums import UserStatus, Schedule, Position
from shared.models import User
from bot.app.states.registration import RegistrationState
from bot.app.keyboards.reply import main_menu
from bot.app.utils.parsing import parse_birth_date
from bot.app.repository.users import UserRepository

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
        is_admin = message.from_user.id in settings.ADMIN_IDS
    except Exception:
        is_admin = False

    approved = user is not None and user.status == UserStatus.APPROVED if user else False

    await message.answer(
        "Добро пожаловать!", reply_markup=main_menu(is_admin=is_admin, approved=approved)
    )


@router.message(F.text == "Зарегистрироваться")
async def start_registration(message: Message, state: FSMContext):
    logging.getLogger(__name__).info("registration start", extra={"tg_id": message.from_user.id})
    async with get_async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_tg_id(message.from_user.id)
    if user:
        if user.status == UserStatus.BLACKLISTED:
            await message.answer("Вы в черном списке и не можете использовать бота.")
            return
        if user.status in (UserStatus.PENDING, UserStatus.APPROVED, UserStatus.REJECTED):
            await message.answer("Вы уже подавали заявку или зарегистрированы.")
            return
    await state.clear()
    await state.set_state(RegistrationState.first_name)
    await message.answer("Введите имя:")


@router.message(RegistrationState.first_name)
async def reg_first_name(message: Message, state: FSMContext):
    logging.getLogger(__name__).debug("step first_name", extra={"tg_id": message.from_user.id, "value": message.text.strip()})
    await state.update_data(first_name=message.text.strip())
    await state.set_state(RegistrationState.last_name)
    await message.answer("Введите фамилию:")


@router.message(RegistrationState.last_name)
async def reg_last_name(message: Message, state: FSMContext):
    logging.getLogger(__name__).debug("step last_name", extra={"tg_id": message.from_user.id, "value": message.text.strip()})
    await state.update_data(last_name=message.text.strip())
    await state.set_state(RegistrationState.birth_date)
    await message.answer("Введите дату рождения в формате ДД.ММ.ГГГГ:")


@router.message(RegistrationState.birth_date)
async def reg_birth_date(message: Message, state: FSMContext):
    d = parse_birth_date(message.text)
    if not d:
        logging.getLogger(__name__).warning("invalid birth_date format", extra={"tg_id": message.from_user.id, "raw": message.text})
        await message.answer("Неверный формат даты. Попробуйте снова: ДД.ММ.ГГГГ")
        return
    logging.getLogger(__name__).debug("step birth_date", extra={"tg_id": message.from_user.id, "value": str(d)})
    await state.update_data(birth_date=d)
    await state.set_state(RegistrationState.rate_k)
    await message.answer("Введите ставку (в тысячах рублей), только число:")


@router.message(RegistrationState.rate_k)
async def reg_rate(message: Message, state: FSMContext):
    try:
        rate = int(message.text.strip())
    except ValueError:
        logging.getLogger(__name__).warning("invalid rate", extra={"tg_id": message.from_user.id, "raw": message.text})
        await message.answer("Введите число.")
        return
    logging.getLogger(__name__).debug("step rate_k", extra={"tg_id": message.from_user.id, "value": rate})
    await state.update_data(rate_k=rate)
    await state.set_state(RegistrationState.schedule)
    await message.answer("Выберите график: 2/2, 5/2, 4/3")


@router.message(RegistrationState.schedule)
async def reg_schedule(message: Message, state: FSMContext):
    val = message.text.strip()
    if val not in {s.value for s in Schedule}:
        logging.getLogger(__name__).warning("invalid schedule", extra={"tg_id": message.from_user.id, "raw": val})
        await message.answer("Неверное значение. Доступно: 2/2, 5/2, 4/3")
        return
    logging.getLogger(__name__).debug("step schedule", extra={"tg_id": message.from_user.id, "value": val})
    await state.update_data(schedule=Schedule(val))
    await state.set_state(RegistrationState.position)
    await message.answer("Выберите должность: Руководитель, Сборщик заказов, Упаковщик, Мастер")


@router.message(RegistrationState.position)
async def reg_position(message: Message, state: FSMContext):
    val = message.text.strip()
    if val not in {p.value for p in Position}:
        logging.getLogger(__name__).warning("invalid position", extra={"tg_id": message.from_user.id, "raw": val})
        await message.answer("Неверная должность. Доступно: Руководитель, Сборщик заказов, Упаковщик, Мастер")
        return
    logging.getLogger(__name__).debug("step position", extra={"tg_id": message.from_user.id, "value": val})
    await state.update_data(position=Position(val))

    data = await state.get_data()
    async with get_async_session() as session:
        repo = UserRepository(session)
        exists = await repo.get_by_tg_id(message.from_user.id)
        if exists and exists.status == UserStatus.BLACKLISTED:
            await message.answer("Вы в черном списке и не можете зарегистрироваться.")
            await state.clear()
            return
        user = await repo.create_pending(
            tg_id=message.from_user.id,
            first_name=data["first_name"],
            last_name=data["last_name"],
            birth_date=data["birth_date"],
            rate_k=data["rate_k"],
            schedule=data["schedule"],
            position=data["position"],
        )

    await state.clear()
    await message.answer("Данные отправлены на рассмотрение администратору.")
    logging.getLogger(__name__).info("registration saved and sent to admins", extra={"tg_id": message.from_user.id, "user_id": user.id})

    # notify admins
    from shared.config import settings
    from aiogram import Bot
    from bot.app.keyboards.inline import approve_reject_kb

    bot = Bot(token=settings.BOT_TOKEN)
    text = (
        f"Новая заявка\n"
        f"TG: {user.tg_id}\n"
        f"Имя: {user.first_name}\n"
        f"Фамилия: {user.last_name}\n"
        f"Дата рождения: {user.birth_date}\n"
        f"Ставка: {user.rate_k}к\n"
        f"График: {user.schedule}\n"
        f"Должность: {user.position}"
    )
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, reply_markup=approve_reject_kb(user.id))
            logging.getLogger(__name__).debug("sent application to admin", extra={"tg_id": message.from_user.id, "admin_tg_id": admin_id})
        except Exception:
            logging.getLogger(__name__).exception("failed to notify admin", extra={"tg_id": message.from_user.id, "admin_tg_id": admin_id})
    await bot.session.close()


@router.message(F.text == "Профиль")
async def profile(message: Message):
    logging.getLogger(__name__).info("profile requested", extra={"tg_id": message.from_user.id})
    async with get_async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_tg_id(message.from_user.id)
    if not user:
        await message.answer("Вы ещё не зарегистрированы.")
        return
    if user.status == UserStatus.BLACKLISTED:
        await message.answer("Вы в черном списке.")
        return
    if user.status != UserStatus.APPROVED:
        await message.answer("Ваша заявка ещё не подтверждена.")
        return
    text = (
        f"Ваш профиль:\n"
        f"Имя: {user.first_name}\n"
        f"Фамилия: {user.last_name}\n"
        f"Дата рождения: {user.birth_date}\n"
        f"Ставка: {user.rate_k}к\n"
        f"График: {user.schedule}\n"
        f"Должность: {user.position}\n"
        f"Статус: {user.status}"
    )
    await message.answer(text)
