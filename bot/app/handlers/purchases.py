import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from datetime import datetime

from shared.config import settings
from shared.db import get_async_session
from shared.enums import UserStatus, PurchaseStatus
from shared.utils import format_date
from bot.app.states.purchases import PurchasesState
from bot.app.keyboards.inline import purchases_cancel_kb, purchases_admin_kb
from bot.app.keyboards.main import main_menu_kb
from bot.app.repository.users import UserRepository
from bot.app.repository.purchases import PurchaseRepository

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.message(F.text.in_({"Закупки", "🛒 Закупки"}))
@router.message(Command("purchases"))
async def purchases_entry(message: Message, state: FSMContext):
    async with get_async_session() as session:
        urepo = UserRepository(session)
        user = await urepo.get_by_tg_id(message.from_user.id)
    if not user:
        await message.answer(
            "ℹ️ Вы не зарегистрированы. Нажмите \"Зарегистрироваться\" ниже.",
            reply_markup=main_menu_kb(None, message.from_user.id),
        )
        return
    if user.status == UserStatus.BLACKLISTED:
        await message.answer(
            "🚫 Доступ ограничен. Вы не можете отправлять заявки на закупку.",
            reply_markup=main_menu_kb(None, message.from_user.id),
        )
        return
    if not (user.status == UserStatus.APPROVED or is_admin(message.from_user.id)):
        await message.answer(
            "⏳ Доступ к разделу \"Закупки\" доступен только одобренным пользователям.",
            reply_markup=main_menu_kb(user.status, message.from_user.id),
        )
        return

    await state.set_state(PurchasesState.waiting_text)
    await message.answer(
        "🛒 <b>Режим закупок</b>\n\n"
        "Опишите, что нужно купить: наименование, количество и пожелания.\n\n"
        "Например: \'Перчатки нитриловые, 100 шт, размер M\'\n\n"
        "Когда будете готовы — просто отправьте сообщение.\n"
        "Если передумали — нажмите кнопку \"Отмена\" ниже.",
        reply_markup=purchases_cancel_kb(),
    )
    logging.getLogger(__name__).info("purchase input started", extra={"tg_id": message.from_user.id})


@router.callback_query(F.data == "purchase:cancel")
async def purchases_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer(
        "❌ <b>Запрос на закупку отменён</b>.\n\n"
        "Если понадобится — вы всегда можете снова открыть раздел \"Закупки\" из меню."
    )
    await cb.answer()
    logging.getLogger(__name__).info("purchase canceled", extra={"tg_id": cb.from_user.id})


@router.message(PurchasesState.waiting_text)
async def purchases_receive_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Пожалуйста, отправьте текст заявки или нажмите \"Отменить\".")
        return

    async with get_async_session() as session:
        urepo = UserRepository(session)
        prepo = PurchaseRepository(session)
        user = await urepo.get_by_tg_id(message.from_user.id)
        if not user or user.status == UserStatus.BLACKLISTED:
            await state.clear()
            await message.answer("Действие недоступно.")
            return
        purchase = await prepo.create(user_id=user.id, text=text)
        logging.getLogger(__name__).info(
            "purchase created", extra={"tg_id": message.from_user.id, "user_id": user.id, "purchase_id": purchase.id}
        )

    await state.clear()
    await message.answer(
        "✅ <b>Заявка отправлена</b>\n\n"
        "Мы приняли ваш запрос и передали его администратору.\n"
        "Вы получите уведомление, как только заявка будет обработана."
    )

    # notify admins chat
    try:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        created_dt = purchase.created_at
        created_str = created_dt.strftime("%d.%m.%Y %H:%M") if isinstance(created_dt, datetime) else ""
        fio = f"{user.first_name or ''} {user.last_name or ''}".strip()
        bd = format_date(user.birth_date)
        admin_text = (
            "🆕 <b>Новая заявка на закупку</b>\n\n"
            f"👤 <b>ФИО:</b> {fio if fio else '—'}\n"
            f"🆔 <b>TG ID:</b> {user.tg_id}\n"
            f"🏷 <b>User ID:</b> {user.id}\n"
            f"⏱ <b>Время создания:</b> {created_str}\n\n"
            f"🛒 <b>Запрос:</b> {purchase.text}"
        )
        chat_id = settings.PURCHASES_CHAT_ID
        if chat_id:
            await bot.send_message(chat_id=chat_id, text=admin_text, reply_markup=purchases_admin_kb(purchase.id))
        else:
            # Fallback: send to each admin if chat id not configured
            for admin_id in settings.admin_ids:
                await bot.send_message(chat_id=admin_id, text=admin_text, reply_markup=purchases_admin_kb(purchase.id))
        await bot.session.close()
    except Exception:
        logging.getLogger(__name__).exception("failed to notify admins about purchase", extra={"purchase_id": purchase.id})


@router.callback_query(F.data.startswith("purchase:"))
async def purchases_admin_actions(cb: CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Недостаточно прав", show_alert=True)
        return
    try:
        _, pid, action = cb.data.split(":", 2)
        purchase_id = int(pid)
    except Exception:
        await cb.answer("Некорректные данные", show_alert=True)
        return

    async with get_async_session() as session:
        prepo = PurchaseRepository(session)
        urepo = UserRepository(session)
        purchase = await prepo.get_by_id(purchase_id)
        if not purchase:
            await cb.answer("Заявка не найдена", show_alert=True)
            return
        user = await urepo.get_by_id(purchase.user_id)
        if action == "done":
            await prepo.update_status(purchase, PurchaseStatus.DONE)
        elif action == "rejected":
            await prepo.update_status(purchase, PurchaseStatus.REJECTED)
        else:
            await cb.answer("Неизвестное действие", show_alert=True)
            return
        logging.getLogger(__name__).info(
            "purchase status updated",
            extra={"admin_tg_id": cb.from_user.id, "purchase_id": purchase.id, "status": purchase.status.value},
        )

    # notify original user
    try:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        now_str = datetime.utcnow().strftime("%d.%m.%Y %H:%M")
        if action == "done":
            text = (
                "✅ <b>Заявка выполнена</b>\n\n"
                "🛒 <b>Запрос:</b>\n"
                f"{purchase.text}\n\n"
                f"⏱ <b>Время:</b> {now_str}"
            )
        else:
            text = (
                "🚫 <b>Заявка отклонена</b>\n\n"
                "🛒 <b>Запрос:</b>\n"
                f"{purchase.text}\n\n"
                f"⏱ <b>Время:</b> {now_str}"
            )
        if user:
            await bot.send_message(user.tg_id, text)
        # update admin message briefly if possible
        try:
            suffix = "✅ Выполнено" if action == "done" else "❌ Отклонено"
            await cb.message.edit_text(cb.message.text + f"\n\n{suffix}")
        except Exception:
            pass
        await bot.session.close()
    except Exception:
        logging.getLogger(__name__).exception("failed to notify user about purchase status", extra={"purchase_id": purchase_id})

    await cb.answer("Готово")
