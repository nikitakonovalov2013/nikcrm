from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from shared.config import settings
from shared.db import get_async_session, add_after_commit_callback
from shared.enums import UserStatus, AdminActionType, Position
from shared.models import MaterialConsumption, MaterialSupply
from shared.services.material_stock import update_stock_on_new_consumption, update_stock_on_new_supply
from shared.services.stock_events_notify import notify_reports_chat_about_stock_event, StockEventActor

from bot.app.keyboards.main import main_menu_kb
from bot.app.keyboards.stocks import (
    materials_page_kb,
    stocks_cancel_kb,
    stocks_confirm_kb,
    stocks_menu_kb,
)
from bot.app.repository.admin_actions import AdminActionRepository
from bot.app.repository.materials import MaterialsRepository
from bot.app.repository.users import UserRepository
from bot.app.states.stocks import StocksState
from shared.permissions import can_manage_stock_op, can_view_stocks, role_flags

router = Router()

MAX_TG_MESSAGE_LEN = 3900


def is_admin(tg_id: int) -> bool:
    return tg_id in settings.admin_ids


def _fmt_stock_line(name: str, qty: Decimal, unit: str) -> str:
    return f"• <b>{name}</b> — {qty} {unit}"


async def _load_user_or_deny(message: Message) -> tuple[bool, UserStatus | None]:
    async with get_async_session() as session:
        urepo = UserRepository(session)
        user = await urepo.get_by_tg_id(message.from_user.id)
    if not user:
        await message.answer(
            "ℹ️ Вы не зарегистрированы. Нажмите \"Зарегистрироваться\" ниже.",
            reply_markup=main_menu_kb(None, message.from_user.id),
        )
        return False, None
    if user.status == UserStatus.BLACKLISTED:
        await message.answer(
            "🚫 Доступ ограничен.",
            reply_markup=main_menu_kb(user.status, message.from_user.id, user.position),
        )
        return False, user.status
    return True, user.status


async def _get_user_status(tg_id: int) -> UserStatus | None:
    async with get_async_session() as session:
        urepo = UserRepository(session)
        user = await urepo.get_by_tg_id(tg_id)
        return user.status if user else None


async def _get_user_for_ops(tg_id: int):
    async with get_async_session() as session:
        urepo = UserRepository(session)
        return await urepo.get_by_tg_id(tg_id)


async def _render_stocks_text(limit: int | None = 8) -> str:
    async with get_async_session() as session:
        mrepo = MaterialsRepository(session)
        materials = await mrepo.list_all()

    if not materials:
        return "📦 <b>Остатки</b>\n\nПока нет материалов."

    lines = ["📦 <b>Остатки</b>", ""]
    show = materials if limit is None else materials[:limit]
    for m in show:
        lines.append(_fmt_stock_line(m.name, Decimal(m.current_stock), m.unit))

    if limit is not None and len(materials) > limit:
        lines.append("")
        lines.append(f"… и ещё {len(materials) - limit}. Нажмите \"Показать всё\".")

    return "\n".join(lines)


async def _render_stocks_menu(tg_id: int, *, expanded: bool) -> tuple[str, object]:
    user = await _get_user_for_ops(tg_id)
    status = user.status if user else None
    r = role_flags(
        tg_id=tg_id,
        admin_ids=settings.admin_ids,
        status=status,
        position=user.position if user else None,
    )
    async with get_async_session() as session:
        mrepo = MaterialsRepository(session)
        materials = await mrepo.list_all()

    limit = None if expanded else 8
    if not materials:
        text = "📦 <b>Остатки</b>\n\nПока нет материалов."
    else:
        lines = ["📦 <b>Остатки</b>", ""]
        show = materials if limit is None else materials[:limit]
        for m in show:
            lines.append(_fmt_stock_line(m.name, Decimal(m.current_stock), m.unit))
        if limit is not None and len(materials) > limit:
            lines.append("")
            lines.append(f"… и ещё {len(materials) - limit}. Нажмите \"Показать всё\".")
        text = "\n".join(lines)

    if len(text) > MAX_TG_MESSAGE_LEN:
        text = text[: MAX_TG_MESSAGE_LEN - 120] + "\n\n… список слишком длинный для Telegram."
    can_toggle = len(materials) > 8 and (r.is_admin or r.is_manager)
    allow_out = bool(r.is_admin or r.is_manager or r.is_master)
    allow_in = bool(r.is_admin or r.is_manager)
    return text, stocks_menu_kb(allow_out=allow_out, allow_in=allow_in, expanded=expanded and can_toggle, can_toggle=can_toggle)


async def _deny_and_back_to_menu(cb: CallbackQuery, state: FSMContext, *, note: str = "⛔ Нет доступа") -> None:
    data = await state.get_data()
    menu_chat_id = data.get("menu_chat_id") or cb.message.chat.id
    menu_message_id = data.get("menu_message_id") or cb.message.message_id
    await state.clear()
    text, kb = await _render_stocks_menu(cb.from_user.id, expanded=False)
    await _edit_message_safe(
        cb,
        chat_id=int(menu_chat_id),
        message_id=int(menu_message_id),
        text=f"{note}.\n\n{text}",
        reply_markup=kb,
    )


async def _edit_message_safe(cb: CallbackQuery, *, chat_id: int, message_id: int, text: str, reply_markup) -> None:
    try:
        await cb.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
        )
        return
    except Exception:
        pass

    # Fallback: if we can't edit (deleted/too old), send a new message.
    try:
        await cb.message.answer(text, reply_markup=reply_markup)
    except Exception:
        pass


@router.message(F.text.in_({"Остатки", "📦 Остатки"}))
@router.message(Command("stocks"))
async def stocks_entry(message: Message, state: FSMContext):
    ok, status = await _load_user_or_deny(message)
    if not ok:
        return
    user = await _get_user_for_ops(message.from_user.id)
    if not can_view_stocks(
        tg_id=message.from_user.id,
        admin_ids=settings.admin_ids,
        status=user.status if user else status,
        position=user.position if user else None,
    ):
        await message.answer(
            "⏳ Раздел \"Остатки\" доступен только одобренным пользователям.",
            reply_markup=main_menu_kb(status, message.from_user.id, user.position if user else None),
        )
        return

    await state.clear()
    text, kb = await _render_stocks_menu(message.from_user.id, expanded=False)
    sent = await message.answer(text, reply_markup=kb)
    await state.update_data(menu_chat_id=sent.chat.id, menu_message_id=sent.message_id, menu_expanded=False)


@router.callback_query(F.data == "stocks:back")
async def stocks_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    user = await _get_user_for_ops(cb.from_user.id)
    status = user.status if user else None
    position = user.position if user else None
    await cb.message.answer(
        "Выберите действие ниже.",
        reply_markup=main_menu_kb(status, cb.from_user.id, position),
    )
    await cb.answer()


@router.callback_query(F.data.in_({"stocks:all", "stocks:compact"}))
async def stocks_toggle_all(cb: CallbackQuery, state: FSMContext):
    expanded = cb.data == "stocks:all"
    user = await _get_user_for_ops(cb.from_user.id)
    if not user or user.status == UserStatus.BLACKLISTED:
        await cb.answer("Действие недоступно", show_alert=True)
        await state.clear()
        return
    r = role_flags(tg_id=cb.from_user.id, admin_ids=settings.admin_ids, status=user.status, position=user.position)
    if not (r.is_admin or r.is_manager):
        await _deny_and_back_to_menu(cb, state)
        await cb.answer()
        return
    text, kb = await _render_stocks_menu(cb.from_user.id, expanded=expanded)
    await state.update_data(menu_chat_id=cb.message.chat.id, menu_message_id=cb.message.message_id, menu_expanded=expanded)
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        # Fallback if message can't be edited
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("stocks:op:"))
async def stocks_choose_op(cb: CallbackQuery, state: FSMContext):
    user = await _get_user_for_ops(cb.from_user.id)
    if not user or user.status == UserStatus.BLACKLISTED:
        await cb.answer("Действие недоступно", show_alert=True)
        await state.clear()
        return

    op = cb.data.split(":", 2)[2]
    if op not in {"in", "out"}:
        await cb.answer("Некорректная операция", show_alert=True)
        return

    if not can_manage_stock_op(
        tg_id=cb.from_user.id,
        admin_ids=settings.admin_ids,
        status=user.status,
        position=user.position,
        op=op,
    ):
        await _deny_and_back_to_menu(cb, state)
        await cb.answer()
        return

    async with get_async_session() as session:
        mrepo = MaterialsRepository(session)
        mats = await mrepo.list_all()

    materials = [(m.id, m.name) for m in mats]
    # Keep reference to the original menu message so cancel can edit it.
    menu_chat_id = cb.message.chat.id
    menu_message_id = cb.message.message_id
    await state.clear()
    await state.set_state(StocksState.choosing_material)
    await state.update_data(
        op=op,
        page=0,
        materials=materials,
        user_id=user.id,
        menu_chat_id=menu_chat_id,
        menu_message_id=menu_message_id,
        menu_expanded=False,
    )

    title = "➕ <b>Пополнение</b>\n\nВыберите материал:" if op == "in" else "➖ <b>Расход</b>\n\nВыберите материал:"
    await cb.message.edit_text(title, reply_markup=materials_page_kb(materials, page=0))
    await cb.answer()


@router.callback_query(StocksState.choosing_material, F.data.startswith("stocks:page:"))
async def stocks_page(cb: CallbackQuery, state: FSMContext):
    try:
        page = int(cb.data.split(":", 2)[2])
    except Exception:
        await cb.answer()
        return

    data = await state.get_data()
    materials = data.get("materials") or []
    await state.update_data(page=page)
    try:
        await cb.message.edit_reply_markup(reply_markup=materials_page_kb(materials, page=page))
    except Exception:
        pass
    await cb.answer()


@router.callback_query(StocksState.choosing_material, F.data.startswith("stocks:mat:"))
async def stocks_pick_material(cb: CallbackQuery, state: FSMContext):
    try:
        material_id = int(cb.data.split(":", 2)[2])
    except Exception:
        await cb.answer("Некорректный материал", show_alert=True)
        return

    data = await state.get_data()
    op = data.get("op")

    async with get_async_session() as session:
        mrepo = MaterialsRepository(session)
        m = await mrepo.get_by_id(material_id)

    if not m:
        await cb.answer("Материал не найден", show_alert=True)
        return

    await state.set_state(StocksState.waiting_amount)
    await state.update_data(material_id=material_id, material_name=m.name, unit=m.unit)

    prefix = "➕" if op == "in" else "➖"
    await cb.message.edit_text(
        f"{prefix} <b>{'Пополнение' if op == 'in' else 'Расход'}</b>\n\n"
        f"Материал: <b>{m.name}</b>\n"
        f"Текущий остаток: {Decimal(m.current_stock)} {m.unit}\n\n"
        f"Введите количество ({m.unit}) числом:",
        reply_markup=stocks_cancel_kb(),
    )
    await cb.answer()


@router.message(StocksState.waiting_amount)
async def stocks_amount_input(message: Message, state: FSMContext):
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer("❌ Введите число (например 1.5).", reply_markup=stocks_cancel_kb())
        return

    if amount <= 0:
        await message.answer("❌ Количество должно быть больше 0.", reply_markup=stocks_cancel_kb())
        return

    data = await state.get_data()
    op = data.get("op")
    material_id = int(data.get("material_id"))

    async with get_async_session() as session:
        mrepo = MaterialsRepository(session)
        m = await mrepo.get_by_id(material_id)

    if not m:
        await state.clear()
        await message.answer("Материал не найден.")
        return

    if op == "out" and amount > Decimal(m.current_stock):
        await message.answer(
            f"❌ Нельзя списать больше, чем есть на складе.\n\nТекущий остаток: {Decimal(m.current_stock)} {m.unit}",
            reply_markup=stocks_cancel_kb(),
        )
        return

    await state.set_state(StocksState.confirming)
    await state.update_data(amount=str(amount), confirm_pending=True)

    prefix = "➕" if op == "in" else "➖"
    await message.answer(
        f"{prefix} <b>{'Пополнение' if op == 'in' else 'Расход'}</b>\n\n"
        f"Материал: <b>{m.name}</b>\n"
        f"Количество: <b>{amount} {m.unit}</b>\n\n"
        "Подтвердить операцию?",
        reply_markup=stocks_confirm_kb(),
    )


@router.callback_query(F.data == "stocks:cancel")
async def stocks_cancel(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    menu_chat_id = data.get("menu_chat_id") or cb.message.chat.id
    menu_message_id = data.get("menu_message_id") or cb.message.message_id
    await state.clear()
    text, kb = await _render_stocks_menu(cb.from_user.id, expanded=False)
    await _edit_message_safe(cb, chat_id=int(menu_chat_id), message_id=int(menu_message_id), text=text, reply_markup=kb)
    await cb.answer("Отменено")


@router.callback_query(StocksState.confirming, F.data == "stocks:confirm")
async def stocks_confirm(cb: CallbackQuery, state: FSMContext):
    user = await _get_user_for_ops(cb.from_user.id)
    if not user or user.status == UserStatus.BLACKLISTED:
        await cb.answer("Действие недоступно", show_alert=True)
        await state.clear()
        return

    data = await state.get_data()
    if not data.get("confirm_pending"):
        await cb.answer("Уже обработано")
        return

    # Idempotency: flip the flag BEFORE doing DB work to avoid double-click duplicates.
    await state.update_data(confirm_pending=False)

    op = data.get("op")
    if op not in {"in", "out"}:
        await cb.answer("Некорректная операция", show_alert=True)
        await state.clear()
        return

    if not can_manage_stock_op(
        tg_id=cb.from_user.id,
        admin_ids=settings.admin_ids,
        status=user.status,
        position=user.position,
        op=str(op),
    ):
        await _deny_and_back_to_menu(cb, state)
        await cb.answer()
        return

    try:
        material_id = int(data.get("material_id"))
        amount = Decimal(str(data.get("amount")))
    except Exception:
        await cb.answer("Некорректные данные", show_alert=True)
        await state.clear()
        return

    if amount <= 0:
        await cb.answer("Некорректное количество", show_alert=True)
        await state.clear()
        return
    user_id = data.get("user_id")
    if not user_id:
        # fallback: resolve user_id by tg_id
        async with get_async_session() as session:
            urepo = UserRepository(session)
            u = await urepo.get_by_tg_id(cb.from_user.id)
            user_id = u.id if u else None
    if not user_id:
        await cb.answer("Пользователь не найден", show_alert=True)
        await state.clear()
        return

    async with get_async_session() as session:
        mrepo = MaterialsRepository(session)
        arepo = AdminActionRepository(session)

        m = await mrepo.get_by_id(material_id)
        if not m:
            await cb.answer("Материал не найден", show_alert=True)
            await state.clear()
            return

        if op == "out" and amount > Decimal(m.current_stock):
            await cb.answer("Недостаточно остатка", show_alert=True)
            # Return to stocks menu without creating operation
            data = await state.get_data()
            menu_chat_id = data.get("menu_chat_id") or cb.message.chat.id
            menu_message_id = data.get("menu_message_id") or cb.message.message_id
            await state.clear()
            text, kb = await _render_stocks_menu(cb.from_user.id, expanded=False)
            await _edit_message_safe(cb, chat_id=int(menu_chat_id), message_id=int(menu_message_id), text=text, reply_markup=kb)
            return

        if op == "out":
            rec = MaterialConsumption(
                material_id=material_id,
                employee_id=int(user_id),
                amount=amount,
                date=date.today(),
            )
            session.add(rec)
            await session.flush()
            await update_stock_on_new_consumption(session, rec)

            actor_name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else "—"
            actor = StockEventActor(name=actor_name or "—", tg_id=cb.from_user.id)
            material_title = m.name
            if getattr(m, "short_name", None):
                material_title = f"{m.name} ({m.short_name})"
            stock_after = Decimal(m.current_stock)
            happened_at = getattr(rec, "created_at", None)
            add_after_commit_callback(
                session,
                lambda: notify_reports_chat_about_stock_event(
                    kind="consumption",
                    material_name=material_title,
                    amount=Decimal(rec.amount),
                    unit=m.unit,
                    actor=actor,
                    happened_at=happened_at,
                    stock_after=stock_after,
                ),
            )
            await arepo.log(
                admin_tg_id=cb.from_user.id,
                user_id=int(user_id),
                action=AdminActionType.EDIT,
                payload={"kind": "consumption", "material_id": material_id, "amount": str(amount)},
            )
            msg = "✅ Списание выполнено"
        else:
            rec = MaterialSupply(
                material_id=material_id,
                employee_id=int(user_id),
                amount=amount,
                date=date.today(),
            )
            session.add(rec)
            await session.flush()
            await update_stock_on_new_supply(session, rec)

            actor_name = f"{user.first_name or ''} {user.last_name or ''}".strip() if user else "—"
            actor = StockEventActor(name=actor_name or "—", tg_id=cb.from_user.id)
            material_title = m.name
            if getattr(m, "short_name", None):
                material_title = f"{m.name} ({m.short_name})"
            stock_after = Decimal(m.current_stock)
            happened_at = getattr(rec, "created_at", None)
            add_after_commit_callback(
                session,
                lambda: notify_reports_chat_about_stock_event(
                    kind="supply",
                    material_name=material_title,
                    amount=Decimal(rec.amount),
                    unit=m.unit,
                    actor=actor,
                    happened_at=happened_at,
                    stock_after=stock_after,
                ),
            )
            await arepo.log(
                admin_tg_id=cb.from_user.id,
                user_id=int(user_id),
                action=AdminActionType.EDIT,
                payload={"kind": "supply", "material_id": material_id, "amount": str(amount)},
            )
            msg = "✅ Пополнение выполнено"

    logging.getLogger(__name__).info(
        "stock operation",
        extra={"tg_id": cb.from_user.id, "op": op, "material_id": material_id, "amount": str(amount)},
    )
    await state.clear()
    text, kb = await _render_stocks_menu(cb.from_user.id, expanded=False)
    # Update the CURRENT message (where confirm button was pressed) back to the main stocks menu.
    try:
        await cb.message.edit_text(f"{msg}.\n\n{text}", reply_markup=kb)
    except Exception:
        await _edit_message_safe(
            cb,
            chat_id=int(cb.message.chat.id),
            message_id=int(cb.message.message_id),
            text=f"{msg}.\n\n{text}",
            reply_markup=kb,
        )
    await cb.answer("Готово")


@router.callback_query(F.data == "stocks:noop")
async def stocks_noop(cb: CallbackQuery):
    await cb.answer()
