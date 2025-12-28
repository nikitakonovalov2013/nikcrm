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
from shared.utils import format_date, format_moscow, utc_now
from bot.app.states.purchases import PurchasesState
from bot.app.keyboards.inline import purchases_cancel_kb, purchases_admin_kb
from bot.app.keyboards.main import main_menu_kb
from bot.app.repository.users import UserRepository
from bot.app.repository.purchases import PurchaseRepository

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def _purchase_admin_text(user, purchase) -> str:
    created_dt = purchase.created_at
    created_str = format_moscow(created_dt) if isinstance(created_dt, datetime) else ""
    fio = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return (
        "🆕 <b>Новая заявка на закупку</b>\n\n"
        f"👤 <b>ФИО:</b> {fio if fio else '—'}\n"
        f"🆔 <b>TG ID:</b> {user.tg_id}\n"
        f"🏷 <b>User ID:</b> {user.id}\n"
        f"⏱ <b>Время создания:</b> {created_str}\n\n"
        f"🛒 <b>Запрос:</b> {purchase.text}"
    )


def _purchase_status_suffix(status: PurchaseStatus) -> str:
    if status == PurchaseStatus.DONE:
        return "✅ Выполнено"
    if status == PurchaseStatus.REJECTED:
        return "❌ Отклонено"
    return "🕒 В ожидании"


def _render_purchase_admin_body(user, purchase) -> str:
    base = _purchase_admin_text(user, purchase)
    suffix = _purchase_status_suffix(purchase.status)
    return base + f"\n\n{suffix}"


def _render_purchase_user_body(purchase, processed_at_str: str) -> str:
    suffix = _purchase_status_suffix(purchase.status)
    title = "✅ <b>Заявка выполнена</b>" if purchase.status == PurchaseStatus.DONE else "🚫 <b>Заявка отклонена</b>"
    return (
        f"{title}\n\n"
        "🛒 <b>Запрос:</b>\n"
        f"{purchase.text or '—'}\n\n"
        f"{suffix}\n"
        f"⏱ <b>Время:</b> {processed_at_str}"
    )


def _caption_safe_payload(full_html: str, limit: int = 1024) -> tuple[str, str | None]:
    if len(full_html) <= limit:
        return full_html, None
    short = (
        "ℹ️ Текст заявки слишком длинный для подписи к фото. "
        "Полное описание — следующим сообщением."
    )
    return short[:limit], full_html


async def _notify_admins_about_purchase(user, purchase) -> None:
    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        admin_text = _purchase_admin_text(user, purchase)
        chat_id = settings.PURCHASES_CHAT_ID
        if chat_id:
            if purchase.photo_file_id:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=purchase.photo_file_id,
                    caption=admin_text,
                    reply_markup=purchases_admin_kb(purchase.id),
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=admin_text,
                    reply_markup=purchases_admin_kb(purchase.id),
                )
        else:
            # Fallback: send to each admin if chat id not configured
            for admin_id in settings.admin_ids:
                if purchase.photo_file_id:
                    await bot.send_photo(
                        chat_id=admin_id,
                        photo=purchase.photo_file_id,
                        caption=admin_text,
                        reply_markup=purchases_admin_kb(purchase.id),
                    )
                else:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=admin_text,
                        reply_markup=purchases_admin_kb(purchase.id),
                    )
    finally:
        await bot.session.close()


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

    await state.set_state(PurchasesState.waiting_input)
    sent = await message.answer(
        "🛒 <b>Режим закупок</b>\n\n"
        "Опишите, что нужно купить: наименование, количество и пожелания.\n\n"
        "Например: \'Перчатки нитриловые, 100 шт, размер M\'\n\n"
        "Когда будете готовы — просто отправьте сообщение или фото с подписью.\n"
        "Если передумали — нажмите кнопку \"Отмена\" ниже.",
        reply_markup=purchases_cancel_kb(),
    )
    await state.update_data(menu_chat_id=sent.chat.id, menu_message_id=sent.message_id)
    logging.getLogger(__name__).info("purchase input started", extra={"tg_id": message.from_user.id})


@router.callback_query(F.data == "purchase:cancel")
async def purchases_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cb.message.edit_text(
            "❌ <b>Запрос на закупку отменён</b>.\n\n"
            "Если понадобится — вы всегда можете снова открыть раздел \"Закупки\" из меню.",
            reply_markup=None,
        )
    except Exception:
        await cb.message.answer(
            "❌ <b>Запрос на закупку отменён</b>.\n\n"
            "Если понадобится — вы всегда можете снова открыть раздел \"Закупки\" из меню."
        )
    await cb.answer()
    logging.getLogger(__name__).info("purchase canceled", extra={"tg_id": cb.from_user.id})


@router.message(PurchasesState.waiting_input)
async def purchases_receive_input(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    photo_file_id = None

    if message.photo:
        photo_file_id = message.photo[-1].file_id
        text = (message.caption or "").strip()

    if photo_file_id and not text:
        await state.set_state(PurchasesState.waiting_text_after_photo)
        await state.update_data(photo_file_id=photo_file_id)
        await message.answer(
            "📸 Фото получено. Теперь отправьте, пожалуйста, <b>текст</b> заявки одним сообщением."
        )
        return

    if not text:
        await message.answer("Пожалуйста, отправьте текст заявки или фото с подписью, или нажмите \"Отменить\".")
        return

    async with get_async_session() as session:
        urepo = UserRepository(session)
        prepo = PurchaseRepository(session)
        user = await urepo.get_by_tg_id(message.from_user.id)
        if not user or user.status == UserStatus.BLACKLISTED:
            await state.clear()
            await message.answer("Действие недоступно.")
            return
        purchase = await prepo.create(user_id=user.id, text=text, photo_file_id=photo_file_id)
        logging.getLogger(__name__).info(
            "purchase created",
            extra={"tg_id": message.from_user.id, "user_id": user.id, "purchase_id": purchase.id},
        )

    await state.clear()
    await message.answer(
        "✅ <b>Заявка отправлена</b>\n\n"
        "Мы приняли ваш запрос и передали его администратору.\n"
        "Вы получите уведомление, как только заявка будет обработана."
    )

    try:
        await _notify_admins_about_purchase(user, purchase)
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to notify admins about purchase", extra={"purchase_id": purchase.id}
        )


@router.message(PurchasesState.waiting_text_after_photo)
async def purchases_receive_text_after_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    stored_photo = data.get("photo_file_id")

    text = (message.text or "").strip()
    photo_file_id = None
    if message.photo:
        photo_file_id = message.photo[-1].file_id
        text = (message.caption or "").strip()
        stored_photo = photo_file_id
        await state.update_data(photo_file_id=photo_file_id)

    if not stored_photo:
        await state.set_state(PurchasesState.waiting_input)
        await message.answer("Пожалуйста, отправьте текст заявки или фото с подписью.")
        return

    if not text:
        await message.answer("Пожалуйста, отправьте текст заявки одним сообщением.")
        return

    async with get_async_session() as session:
        urepo = UserRepository(session)
        prepo = PurchaseRepository(session)
        user = await urepo.get_by_tg_id(message.from_user.id)
        if not user or user.status == UserStatus.BLACKLISTED:
            await state.clear()
            await message.answer("Действие недоступно.")
            return
        purchase = await prepo.create(user_id=user.id, text=text, photo_file_id=stored_photo)
        logging.getLogger(__name__).info(
            "purchase created",
            extra={"tg_id": message.from_user.id, "user_id": user.id, "purchase_id": purchase.id},
        )

    await state.clear()
    await message.answer(
        "✅ <b>Заявка отправлена</b>\n\n"
        "Мы приняли ваш запрос и передали его администратору.\n"
        "Вы получите уведомление, как только заявка будет обработана."
    )

    try:
        await _notify_admins_about_purchase(user, purchase)
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to notify admins about purchase", extra={"purchase_id": purchase.id}
        )


@router.callback_query(F.data.startswith("purchase:"))
async def purchases_admin_actions(cb: CallbackQuery):
    if cb.data == "purchase:cancel":
        return
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

    processed_at_str = format_moscow(utc_now())

    # update message in purchases chat (do not rely on current caption/text)
    try:
        admin_body = _render_purchase_admin_body(user, purchase) if user else (cb.message.caption or cb.message.text or "")
        if purchase.photo_file_id:
            caption, _ = _caption_safe_payload(admin_body)
            await cb.bot.edit_message_caption(
                chat_id=cb.message.chat.id,
                message_id=cb.message.message_id,
                caption=caption,
                reply_markup=None,
            )
        else:
            await cb.bot.edit_message_text(
                chat_id=cb.message.chat.id,
                message_id=cb.message.message_id,
                text=admin_body,
                reply_markup=None,
            )
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to update purchase message in chat",
            extra={"purchase_id": purchase_id, "has_photo": bool(purchase.photo_file_id)},
        )

    # notify original user (include photo if present)
    try:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        user_body = _render_purchase_user_body(purchase, processed_at_str)
        if user:
            if purchase.photo_file_id:
                caption, extra = _caption_safe_payload(user_body)
                await bot.send_photo(user.tg_id, photo=purchase.photo_file_id, caption=caption)
                if extra:
                    await bot.send_message(user.tg_id, extra)
            else:
                await bot.send_message(user.tg_id, user_body)
        await bot.session.close()
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to notify user about purchase status",
            extra={"purchase_id": purchase_id, "has_photo": bool(purchase.photo_file_id)},
        )

    await cb.answer("Готово")
