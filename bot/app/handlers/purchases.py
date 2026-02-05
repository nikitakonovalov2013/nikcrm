import logging
import asyncio
from pathlib import Path
from uuid import uuid4

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.types import FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from datetime import datetime

from shared.config import settings
from shared.db import get_async_session
from shared.enums import UserStatus, PurchaseStatus
from shared.models import PurchaseEvent
from shared.utils import format_date, format_moscow, utc_now
from bot.app.utils.telegram import send_html
from bot.app.guards.user_guard import ensure_registered_or_reply
from bot.app.states.purchases import PurchasesState
from bot.app.keyboards.inline import purchases_cancel_kb, purchases_priority_kb, purchases_workflow_kb
from bot.app.keyboards.main import main_menu_kb
from bot.app.repository.users import UserRepository
from bot.app.repository.purchases import PurchaseRepository
from shared.services.purchases_domain import purchase_take_in_work, purchase_cancel, purchase_mark_bought
from shared.services.purchases_render import purchases_chat_message_text, purchase_created_user_message
from bot.app.services.telegram_outbox import enqueue_purchase_notify, telegram_outbox_job

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def _purchase_priority_human(priority: str | None) -> str:
    p = str(priority or "").strip().lower()
    if p == "urgent":
        return "🔥 Срочно"
    return "Обычный"


def _purchase_admin_text(user, purchase) -> str:
    created_dt = purchase.created_at
    created_str = format_moscow(created_dt) if isinstance(created_dt, datetime) else ""
    fio = f"{user.first_name or ''} {user.last_name or ''}".strip()
    pr = _purchase_priority_human(getattr(purchase, "priority", None))
    return (
        f"🛒 <b>Закупка #{int(purchase.id)}</b>\n\n"
        f"🛒 <b>Запрос:</b> {purchase.text or '—'}\n"
        f"👤 <b>Кто создал:</b> {fio if fio else '—'}\n"
        f"⏱ <b>Когда создал:</b> {created_str or '—'}\n"
        f"⚡ <b>Приоритет:</b> {pr}"
    )


def _fio(u) -> str:
    if not u:
        return "—"
    name = (
        " ".join([str(getattr(u, "first_name", "") or "").strip(), str(getattr(u, "last_name", "") or "").strip()]).strip()
    )
    return name or f"#{int(getattr(u, 'id', 0) or 0)}"


def _purchase_status_ru(status: PurchaseStatus) -> str:
    if status == PurchaseStatus.NEW:
        return "Новые"
    if status == PurchaseStatus.IN_PROGRESS:
        return "В работе"
    if status == PurchaseStatus.BOUGHT:
        return "Куплено"
    if status == PurchaseStatus.CANCELED:
        return "Отменено"
    return "—"


def _purchase_caption_safe(full_html: str, limit: int = 1024) -> tuple[str, str | None]:
    if len(full_html) <= limit:
        return full_html, None
    short = (
        "ℹ️ Текст заявки слишком длинный для подписи к фото. "
        "Полное описание — следующим сообщением."
    )
    return short[:limit], full_html


def _purchase_photo_key_from_filename(filename: str) -> str:
    ext = Path(str(filename or "")).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"
    name = f"{uuid4().hex}{ext}"
    return f"purchases/{name}"


def _purchase_photo_fs_path_from_key(photo_key: str) -> Path:
    key = str(photo_key).lstrip("/")
    return (Path(__file__).resolve().parents[3] / "web" / "app" / "static" / "uploads" / key)


def _purchase_photo_path_from_key(photo_key: str | None) -> str | None:
    if not photo_key:
        return None
    key = str(photo_key).lstrip("/")
    return f"/crm/static/uploads/{key}"


async def _download_tg_photo_to_uploads(*, bot: Bot, tg_file_id: str) -> tuple[str, str]:
    file = await bot.get_file(tg_file_id)
    photo_key = _purchase_photo_key_from_filename(getattr(file, "file_path", "") or "")
    fs_path = _purchase_photo_fs_path_from_key(photo_key)
    fs_path.parent.mkdir(parents=True, exist_ok=True)

    await bot.download_file(getattr(file, "file_path"), destination=fs_path)
    photo_path = _purchase_photo_path_from_key(photo_key)
    if not photo_path:
        raise RuntimeError("failed to build purchase photo_path")
    return str(photo_key), str(photo_path)


async def _send_purchase_status_to_purchases_chat(*, user, purchase) -> None:
    chat_id = int(getattr(settings, "PURCHASES_CHAT_ID", 0) or 0)
    if chat_id == 0:
        logging.getLogger(__name__).warning(
            "PURCHASES_CHAT_ID is not configured, skipping purchases notify",
            extra={"chat_id": int(chat_id)},
        )
        return

    purchase_id = int(getattr(purchase, "id", 0) or 0)
    if purchase_id <= 0:
        return

    # IMPORTANT: do not use detached ORM instances for rendering (lazy-load relations will fail).
    # Reload purchase + relations from DB inside our own session.
    async with get_async_session() as session:
        prepo = PurchaseRepository(session)
        urepo = UserRepository(session)
        p2 = await prepo.get_by_id_full(purchase_id)
        if not p2:
            return
        u2 = await urepo.get_by_id(int(getattr(p2, "user_id", 0) or 0))

        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        try:
            text = purchases_chat_message_text(user=u2, purchase=p2)
            kb = purchases_workflow_kb(purchase_id=int(p2.id), status=getattr(p2, "status", PurchaseStatus.NEW))

            tg_file_id = str(getattr(p2, "tg_photo_file_id", None) or getattr(p2, "photo_file_id", None) or "").strip()
            photo_path = str(getattr(p2, "photo_path", None) or "").strip()
            photo_url = str(getattr(p2, "photo_url", None) or "").strip()

            caption, extra_text = _purchase_caption_safe(str(text))

            if tg_file_id:
                sent = await bot.send_photo(chat_id=chat_id, photo=tg_file_id, caption=caption, reply_markup=kb)
                if extra_text:
                    await bot.send_message(chat_id=chat_id, text=extra_text)
            elif photo_path:
                # photo_path stored as /crm/static/uploads/...
                rel = str(photo_path).replace("/crm/static/uploads/", "").lstrip("/")
                fs_path = (Path(__file__).resolve().parents[3] / "web" / "app" / "static" / "uploads" / rel)
                sent = await bot.send_photo(chat_id=chat_id, photo=FSInputFile(str(fs_path)), caption=caption, reply_markup=kb)
                if extra_text:
                    await bot.send_message(chat_id=chat_id, text=extra_text)
            elif photo_url:
                sent = await bot.send_photo(chat_id=chat_id, photo=photo_url, caption=caption, reply_markup=kb)
                if extra_text:
                    await bot.send_message(chat_id=chat_id, text=extra_text)
            else:
                sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=kb)

            try:
                await prepo.update_tg_message_link(
                    purchase_id=int(p2.id),
                    tg_chat_id=int(chat_id),
                    tg_message_id=int(getattr(sent, "message_id", 0) or 0),
                )
            except Exception:
                logging.getLogger(__name__).exception(
                    "failed to save purchase tg message link",
                    extra={"purchase_id": int(p2.id)},
                )
        finally:
            await bot.session.close()


def _purchase_status_suffix(status: PurchaseStatus) -> str:
    if status == PurchaseStatus.NEW:
        return "🆕 Новый"
    if status == PurchaseStatus.IN_PROGRESS:
        return "🛠 В работе"
    if status == PurchaseStatus.BOUGHT:
        return "✅ Куплено"
    if status == PurchaseStatus.CANCELED:
        return "❌ Отменено"
    return "—"


def _render_purchase_admin_body(user, purchase) -> str:
    base = _purchase_admin_text(user, purchase)
    suffix = _purchase_status_suffix(purchase.status)
    # Show last comment if exists (from web or bot)
    try:
        events = list(getattr(purchase, "events", None) or [])
    except Exception:
        events = []
    comments = [e for e in events if str(getattr(e, "type", "") or "") == "comment" and str(getattr(e, "text", "") or "").strip()]
    last_comment = None
    if comments:
        try:
            last_comment = sorted(comments, key=lambda x: getattr(x, "created_at", None) or utc_now())[-1]
        except Exception:
            last_comment = comments[-1]

    extra = ""
    if last_comment is not None:
        try:
            who_u = getattr(last_comment, "actor_user", None)
            who = (
                f"{(getattr(who_u, 'first_name', '') or '').strip()} {(getattr(who_u, 'last_name', '') or '').strip()}".strip()
                if who_u is not None
                else "—"
            )
        except Exception:
            who = "—"
        try:
            when = format_moscow(getattr(last_comment, "created_at", None))
        except Exception:
            when = ""
        txt = str(getattr(last_comment, "text", "") or "").strip()
        extra = f"\n\n💬 <b>Последний комментарий</b>\n👤 {who}\n⏱ {when}\n{txt}"

    return base + extra + f"\n\n{suffix}"


def _render_purchase_user_body(purchase, processed_at_str: str) -> str:
    suffix = _purchase_status_suffix(purchase.status)
    if purchase.status == PurchaseStatus.BOUGHT:
        title = "✅ <b>Закупка куплена</b>"
    elif purchase.status == PurchaseStatus.CANCELED:
        title = "❌ <b>Закупка отменена</b>"
    elif purchase.status == PurchaseStatus.IN_PROGRESS:
        title = "� <b>Закупка в работе</b>"
    else:
        title = "🆕 <b>Новая закупка</b>"
    pr = _purchase_priority_human(getattr(purchase, "priority", None))
    return (
        f"{title}\n\n"
        f"🛒 <b>Закупка #{int(purchase.id)}</b>\n\n"
        "🛒 <b>Запрос:</b>\n"
        f"{purchase.text or '—'}\n\n"
        f"⚡ <b>Приоритет:</b> {pr}\n"
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
    # Deprecated: purchases notifications must go ONLY to PURCHASES_CHAT_ID.
    try:
        pid = int(getattr(purchase, "id", 0) or 0)
        if pid > 0:
            await enqueue_purchase_notify(purchase_id=int(pid))
            try:
                asyncio.create_task(telegram_outbox_job())
            except Exception:
                pass
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to enqueue purchase notify",
            extra={"purchase_id": int(getattr(purchase, "id", 0) or 0)},
        )


async def _notify_purchase_creator_status(*, purchase_id: int) -> None:
    pid = int(purchase_id)
    if pid <= 0:
        return

    async with get_async_session() as session:
        prepo = PurchaseRepository(session)
        urepo = UserRepository(session)
        p = await prepo.get_by_id_full(pid)
        if not p:
            return
        u = await urepo.get_by_id(int(getattr(p, "user_id", 0) or 0))
        if not u:
            return

    tg_id = int(getattr(u, "tg_id", 0) or 0)
    if tg_id <= 0:
        return

    st = getattr(p, "status", None)
    st_val = st.value if hasattr(st, "value") else str(st or "")
    purchase_text = str(getattr(p, "text", "") or "").strip() or "—"

    if st_val == PurchaseStatus.IN_PROGRESS.value:
        body = f"☑️ Ваша заявка на закупку № {pid} взята в работу!\n\n{purchase_text}"
    elif st_val == PurchaseStatus.CANCELED.value:
        body = f"❌ Ваша заявка на закупку № {pid} отклонена!\n\n{purchase_text}"
    elif st_val == PurchaseStatus.BOUGHT.value:
        body = f"✅ Ваша заявка на закупку № {pid} выполнена!\n\n{purchase_text}"
    else:
        return

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        await bot.send_message(chat_id=tg_id, text=body)
    finally:
        await bot.session.close()


@router.message(F.text.in_({"Закупки", "🛒 Закупки"}))
@router.message(Command("purchases"))
async def purchases_entry(message: Message, state: FSMContext):
    user = await ensure_registered_or_reply(message)
    if not user:
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
            reply_markup=main_menu_kb(user.status, message.from_user.id, user.position),
        )
        return

    await state.set_state(PurchasesState.waiting_priority)
    sent = await message.answer(
        "Раздел «Закупки» создан для того, чтобы вы могли оперативно сообщать о том, что необходимо закупить для работы. 🪡\n\n"
        "<i>Если вы видите, что инструмент, материал или расходник закончился и это повлечет остановку производства:\n"
        "создавайте заявку в этом разделе.</i>\n\n"
        "⚡️ Заявка сразу приходит руководству и по приходу вы получите уведомление об успешной закупке.\n\n"
        "❌ Личные просьбы отклоняются, например: личные кружки, стулья «помягче».\n\n"
        "Выберите приоритет заявки:",
        reply_markup=purchases_priority_kb(),
    )
    await state.update_data(menu_chat_id=sent.chat.id, menu_message_id=sent.message_id)
    logging.getLogger(__name__).info("purchase input started", extra={"tg_id": message.from_user.id})


@router.callback_query(F.data.startswith("purchase:priority:"))
async def purchases_choose_priority(cb: CallbackQuery, state: FSMContext):
    try:
        val = str(cb.data).split(":", 2)[2] if cb.data else ""
        pr = "urgent" if val == "urgent" else "normal"
        await state.update_data(draft_priority=str(pr))
        await state.set_state(PurchasesState.waiting_input)

        try:
            await cb.message.edit_text(
                "🛒 Закупки\n\n"
                "Опишите, что нужно купить.\n\n"
                "Например: \"Перчатки нитриловые, 100 шт, размер М\"\n\n"
                "Можно отправить фото с подписью.\n"
                "Если передумали — нажмите кнопку \"Отмена\" ниже.\n\n"
                "Напишите, что требуется закупить, а к тексту дополнительно\n"
                "можно прикрепить фото! 📸 После заявка отправиться\n"
                "руководству. 🚀\n\n"
                "✅ Например: перчатки 100шт размер М",
                reply_markup=purchases_cancel_kb(),
            )
        except Exception:
            await cb.message.answer(
                "🛒 Закупки\n\n"
                "Опишите, что нужно купить.\n\n"
                "Например: \"Перчатки нитриловые, 100 шт, размер М\"\n\n"
                "Можно отправить фото с подписью.\n"
                "Если передумали — нажмите кнопку \"Отмена\" ниже.\n\n"
                "Напишите, что требуется закупить, а к тексту дополнительно\n"
                "можно прикрепить фото! 📸 После заявка отправиться\n"
                "руководству. 🚀\n\n"
                "✅ Например: перчатки 100шт размер М",
                reply_markup=purchases_cancel_kb(),
            )
    finally:
        try:
            await cb.answer()
        except Exception:
            pass


@router.callback_query(F.data == "purchase:cancel")
async def purchases_cancel(cb: CallbackQuery, state: FSMContext):
    try:
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
        logging.getLogger(__name__).info("purchase canceled", extra={"tg_id": cb.from_user.id})
    finally:
        try:
            await cb.answer()
        except Exception:
            pass


@router.message(PurchasesState.waiting_input)
async def purchases_receive_input(message: Message, state: FSMContext):
    data = await state.get_data()
    draft_priority = str(data.get("draft_priority") or "normal")
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

    user = None
    purchase = None
    try:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        downloaded = None
        try:
            if photo_file_id:
                downloaded = await _download_tg_photo_to_uploads(bot=bot, tg_file_id=str(photo_file_id))
        finally:
            await bot.session.close()

        photo_key = downloaded[0] if downloaded else None
        photo_path = downloaded[1] if downloaded else None
        async with get_async_session() as session:
            urepo = UserRepository(session)
            prepo = PurchaseRepository(session)
            user = await urepo.get_or_create_minimal_by_tg_id(
                tg_id=int(message.from_user.id),
                first_name=(getattr(message.from_user, "first_name", None) if message.from_user else None),
                last_name=(getattr(message.from_user, "last_name", None) if message.from_user else None),
            )
            if not user or user.status == UserStatus.BLACKLISTED:
                await message.answer("Действие недоступно.")
                return

            purchase = await prepo.create(
                user_id=int(user.id),
                text=text,
                photo_file_id=photo_file_id,
                tg_photo_file_id=str(photo_file_id) if photo_file_id else None,
                photo_key=photo_key,
                photo_path=photo_path,
                priority=draft_priority,
            )
            logging.getLogger(__name__).info(
                "purchase created",
                extra={"tg_id": message.from_user.id, "user_id": int(user.id), "purchase_id": int(purchase.id)},
            )

        await message.answer(
            purchase_created_user_message(purchase_id=int(purchase.id))
        )
        try:
            await enqueue_purchase_notify(purchase_id=int(purchase.id))
            try:
                asyncio.create_task(telegram_outbox_job())
            except Exception:
                pass
        except Exception:
            logging.getLogger(__name__).exception(
                "failed to enqueue purchases chat notify",
                extra={"purchase_id": int(getattr(purchase, "id", 0) or 0)},
            )
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to create purchase from bot",
            extra={"tg_id": int(message.from_user.id)},
        )
        await message.answer("❌ Не удалось создать закупку. Попробуйте ещё раз.")
    finally:
        try:
            await state.clear()
        except Exception:
            pass


@router.message(PurchasesState.waiting_text_after_photo)
async def purchases_receive_text_after_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    stored_photo = data.get("photo_file_id")
    draft_priority = str(data.get("draft_priority") or "normal")

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

    user = None
    purchase = None
    try:
        bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
        downloaded = None
        try:
            if stored_photo:
                downloaded = await _download_tg_photo_to_uploads(bot=bot, tg_file_id=str(stored_photo))
        finally:
            await bot.session.close()

        photo_key = downloaded[0] if downloaded else None
        photo_path = downloaded[1] if downloaded else None
        async with get_async_session() as session:
            urepo = UserRepository(session)
            prepo = PurchaseRepository(session)
            user = await urepo.get_or_create_minimal_by_tg_id(
                tg_id=int(message.from_user.id),
                first_name=(getattr(message.from_user, "first_name", None) if message.from_user else None),
                last_name=(getattr(message.from_user, "last_name", None) if message.from_user else None),
            )
            if not user or user.status == UserStatus.BLACKLISTED:
                await message.answer("Действие недоступно.")
                return
            purchase = await prepo.create(
                user_id=int(user.id),
                text=text,
                photo_file_id=str(stored_photo),
                tg_photo_file_id=str(stored_photo) if stored_photo else None,
                photo_key=photo_key,
                photo_path=photo_path,
                priority=draft_priority,
            )
            logging.getLogger(__name__).info(
                "purchase created",
                extra={"tg_id": message.from_user.id, "user_id": int(user.id), "purchase_id": int(purchase.id)},
            )

        await message.answer(
            purchase_created_user_message(purchase_id=int(purchase.id))
        )
        try:
            await enqueue_purchase_notify(purchase_id=int(purchase.id))
            try:
                asyncio.create_task(telegram_outbox_job())
            except Exception:
                pass
        except Exception:
            logging.getLogger(__name__).exception(
                "failed to enqueue purchases chat notify",
                extra={"purchase_id": int(getattr(purchase, "id", 0) or 0)},
            )
    except Exception:
        logging.getLogger(__name__).exception(
            "failed to create purchase (text after photo) from bot",
            extra={"tg_id": int(message.from_user.id)},
        )
        await message.answer("❌ Не удалось создать закупку. Попробуйте ещё раз.")
    finally:
        try:
            await state.clear()
        except Exception:
            pass


@router.callback_query(F.data.startswith("purchase:"))
async def purchases_admin_actions(cb: CallbackQuery):
    # Always release inline button spinner quickly.
    try:
        await cb.answer()
    except Exception:
        pass
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

    try:
        async with get_async_session() as session:
            prepo = PurchaseRepository(session)
            urepo = UserRepository(session)

            actor = await urepo.get_or_create_minimal_by_tg_id(
                tg_id=int(cb.from_user.id),
                first_name=(getattr(cb.from_user, "first_name", None) if cb.from_user else None),
                last_name=(getattr(cb.from_user, "last_name", None) if cb.from_user else None),
            )
            if actor is None:
                await cb.answer("Не удалось определить пользователя. Попробуйте /start", show_alert=True)
                return

            # Lock row and apply transition in shared domain logic.
            if action == "take":
                result = await purchase_take_in_work(session=session, purchase_id=int(purchase_id), actor_user_id=int(actor.id))
                etype = "taken"
            elif action == "bought":
                result = await purchase_mark_bought(session=session, purchase_id=int(purchase_id), actor_user_id=int(actor.id))
                etype = "bought"
            elif action == "cancel":
                result = await purchase_cancel(session=session, purchase_id=int(purchase_id), actor_user_id=int(actor.id))
                etype = "canceled"
            else:
                await cb.answer("Неизвестное действие", show_alert=True)
                return

            # If no-op (already in desired status) -> do not spam chat.
            if not bool(getattr(result, "changed", False)):
                await cb.answer("Уже обновлено", show_alert=True)
                return

            # Reload full purchase for event + notify.
            purchase = await prepo.get_by_id_full(int(purchase_id))
            if not purchase:
                await cb.answer("Заявка не найдена", show_alert=True)
                return
            user = await urepo.get_by_id(int(getattr(purchase, "user_id", 0) or 0))

            session.add(
                PurchaseEvent(
                    purchase_id=int(purchase.id),
                    actor_user_id=int(actor.id),
                    type=str(etype),
                    text=None,
                    payload=None,
                )
            )
            await session.flush()

        # After commit: send NEW message to purchases chat (no edits)
        try:
            await enqueue_purchase_notify(purchase_id=int(purchase_id))
            try:
                asyncio.create_task(telegram_outbox_job())
            except Exception:
                pass
        except Exception:
            logging.getLogger(__name__).exception(
                "failed to enqueue purchases chat notify",
                extra={"purchase_id": int(purchase_id)},
            )

        try:
            await _notify_purchase_creator_status(purchase_id=int(purchase_id))
        except Exception:
            logging.getLogger(__name__).exception(
                "failed to notify purchase creator",
                extra={"purchase_id": int(purchase_id)},
            )

        await cb.answer("✅ Обновлено")
    finally:
        try:
            if not cb.answered:
                await cb.answer()
        except Exception:
            pass
