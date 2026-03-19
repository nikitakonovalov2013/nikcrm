"""Finance bot handlers — simplified UX, inline cancel/skip, category cache."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from sqlalchemy import select

from shared.config import settings
from shared.db import get_async_session
from shared.enums import Position, UserStatus
from shared.models import User
from shared.services.finance_service import (
    create_category as _create_cat,
    create_operation as _create_op,
    get_dashboard as _get_dash,
    list_categories as _list_cats,
    list_operations as _list_ops,
)

log = logging.getLogger(__name__)
router = Router()

ALLOWED_TG_IDS: set[int] = set()
_CANCEL_TEXT = "❌ Отмена"

# ── Category cache ─────────────────────────────────────────────────────────────
_cat_cache: dict[str, tuple[list, float]] = {}
_CAT_CACHE_TTL = 60.0


async def _get_cats(op_type: str) -> list:
    now = time.monotonic()
    if op_type in _cat_cache:
        cats, ts = _cat_cache[op_type]
        if now - ts < _CAT_CACHE_TTL:
            return cats
    async with get_async_session() as s:
        cats = list(await _list_cats(session=s, type_filter=op_type, include_archived=False))
    _cat_cache[op_type] = (cats, now)
    return cats


def _invalidate_cat_cache() -> None:
    _cat_cache.clear()


# ── FSM ───────────────────────────────────────────────────────────────────────
class AddOpFSM(StatesGroup):
    amount = State()
    category = State()
    category_new = State()
    comment = State()
    attachment = State()
    confirm = State()


# ── Formatters ────────────────────────────────────────────────────────────────
def _fmt_money(v) -> str:
    try:
        n = float(v)
        return f"{n:,.2f}".replace(",", " ").replace(".", ",") + " ₽"
    except Exception:
        return "—"


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%d.%m %H:%M")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ── Keyboards ─────────────────────────────────────────────────────────────────
def _menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➖ Расход"), KeyboardButton(text="➕ Доход")],
            [KeyboardButton(text="📊 Сводка за сегодня"), KeyboardButton(text="📊 Сводка за месяц")],
            [KeyboardButton(text="🧾 Последние операции")],
        ],
        resize_keyboard=True,
    )


def _cancel_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="fin:cancel")],
    ])


def _skip_cancel_inline(ctx: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"fin:skip_{ctx}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="fin:cancel"),
        ],
    ])


def _category_inline(cats: list, *, show_all: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    display = cats if show_all else cats[:10]
    for c in display:
        rows.append([InlineKeyboardButton(text=str(c.name), callback_data=f"fcat:{int(c.id)}")])
    if not show_all and len(cats) > 10:
        rows.append([InlineKeyboardButton(text="� Показать все", callback_data="fcat_all")])
    rows.append([InlineKeyboardButton(text="⌨️ Ввести вручную", callback_data="fcat_new")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="fin:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _confirm_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Сохранить", callback_data="fop_save")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="fin:cancel")],
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────
async def _answer_cb(cb: CallbackQuery, text: str = "") -> None:
    try:
        await cb.answer(text or None)
    except TelegramBadRequest:
        pass


async def _edit_or_ignore(msg: Message, text: str) -> None:
    try:
        await msg.edit_text(text)
    except TelegramBadRequest:
        pass


# ── Access control ────────────────────────────────────────────────────────────
async def _is_allowed(tg_id: int) -> bool:
    if tg_id in ALLOWED_TG_IDS:
        return True
    if tg_id in set(getattr(settings, "admin_ids", None) or []):
        return True
    try:
        async with get_async_session() as s:
            user = (
                await s.execute(select(User).where(User.tg_id == int(tg_id)).where(User.is_deleted == False))
            ).scalars().first()
        if user is None:
            return False
        return (user.status == UserStatus.APPROVED) and (user.position == Position.MANAGER)
    except Exception:
        log.exception("finance bot access check failed")
        return False


async def _guard(msg: Message, state: FSMContext | None = None) -> bool:
    if not await _is_allowed(int(msg.from_user.id)):
        if state is not None:
            await state.clear()
        await msg.answer("Нет доступа.")
        return False
    return True


# ── Ask helpers ───────────────────────────────────────────────────────────────
async def _ask_amount(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    emoji = "➕" if data.get("op_type") == "income" else "➖"
    await msg.answer(
        f"{emoji} <b>Введите сумму в рублях</b> 💳\n\n"
        "Например: 6500",
        reply_markup=_cancel_inline(),
    )
    await state.set_state(AddOpFSM.amount)


async def _ask_category(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    op_type = str(data.get("op_type") or "expense")
    cats = await _get_cats(op_type)
    label = "дохода" if op_type == "income" else "расхода"
    await msg.answer(
        f"📂 <b>Выберите категорию {label}</b>",
        reply_markup=_category_inline(cats),
    )
    await state.set_state(AddOpFSM.category)


async def _ask_comment(msg: Message, state: FSMContext) -> None:
    await msg.answer(
        "Добавьте комментарий (необязательно) ✍️\n\n"
        "Или нажмите «Пропустить»",
        reply_markup=_skip_cancel_inline("cmt"),
    )
    await state.set_state(AddOpFSM.comment)


async def _ask_attachment(msg: Message, state: FSMContext) -> None:
    await msg.answer(
        "Можно прикрепить фото чека 🧾\n\n"
        "Или нажмите «Пропустить»",
        reply_markup=_skip_cancel_inline("att"),
    )
    await state.set_state(AddOpFSM.attachment)


async def _ask_confirm(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    op_type = str(data.get("op_type") or "expense")
    emoji = "➕" if op_type == "income" else "➖"
    label = "Доход" if op_type == "income" else "Расход"
    n_files = len(data.get("tg_file_ids") or [])
    preview = (
        "<b>Проверьте перед сохранением</b>\n\n"
        f"{emoji} Тип: <b>{label}</b>\n"
        f"💰 Сумма: <b>{_fmt_money(data.get('amount'))}</b>\n"
        f"📂 Категория: <b>{data.get('category_name') or 'Без категории'}</b>\n"
        f"✍️ Комментарий: <b>{data.get('comment') or '—'}</b>\n"
        f"📅 Дата: <b>{_fmt_dt(_utc_now())}</b>\n"
        f"🧾 Вложений: <b>{n_files if n_files else '—'}</b>"
    )
    await msg.answer(preview, reply_markup=_confirm_inline())
    await state.set_state(AddOpFSM.confirm)


# ── Commands ──────────────────────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _guard(msg, state):
        return
    await msg.answer(
        "💰 <b>Финансы</b>\n\n"
        "Добавляйте доходы и расходы в пару шагов.",
        reply_markup=_menu_kb(),
    )


@router.message(Command("menu"))
async def cmd_menu(msg: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _guard(msg, state):
        return
    await msg.answer("Главное меню:", reply_markup=_menu_kb())


@router.message(F.text.in_({_CANCEL_TEXT, "/cancel"}))
async def cancel_any(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("Операция отменена ✅", reply_markup=_menu_kb())


# ── Inline cancel — any AddOpFSM state ───────────────────────────────────────
@router.callback_query(
    StateFilter(
        AddOpFSM.amount, AddOpFSM.category, AddOpFSM.category_new,
        AddOpFSM.comment, AddOpFSM.attachment, AddOpFSM.confirm,
    ),
    F.data == "fin:cancel",
)
async def inline_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await _answer_cb(cb)
    await state.clear()
    await _edit_or_ignore(cb.message, "Операция отменена ✅")


# ── Start income / expense ────────────────────────────────────────────────────
@router.message(F.text == "➕ Доход")
async def start_income(msg: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _guard(msg, state):
        return
    await state.update_data(op_type="income", tg_file_ids=[])
    await _ask_amount(msg, state)


@router.message(F.text == "➖ Расход")
async def start_expense(msg: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _guard(msg, state):
        return
    await state.update_data(op_type="expense", tg_file_ids=[])
    await _ask_amount(msg, state)


# ── Step: amount ──────────────────────────────────────────────────────────────
@router.message(AddOpFSM.amount)
async def step_amount(msg: Message, state: FSMContext) -> None:
    if (msg.text or "").strip() in {_CANCEL_TEXT, "/cancel"}:
        await cancel_any(msg, state)
        return
    raw = (msg.text or "").strip().replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(raw)
        if amount <= 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        await msg.answer(
            "Не понял сумму. Введите число, например: 6500",
            reply_markup=_cancel_inline(),
        )
        return
    await state.update_data(amount=str(amount))
    await _ask_category(msg, state)


# ── Step: category ────────────────────────────────────────────────────────────
@router.callback_query(AddOpFSM.category, F.data == "fcat_all")
async def cat_show_all(cb: CallbackQuery, state: FSMContext) -> None:
    await _answer_cb(cb)
    data = await state.get_data()
    op_type = str(data.get("op_type") or "expense")
    cats = await _get_cats(op_type)
    try:
        await cb.message.edit_reply_markup(reply_markup=_category_inline(cats, show_all=True))
    except TelegramBadRequest:
        await cb.message.answer(
            "📂 <b>Все категории:</b>",
            reply_markup=_category_inline(cats, show_all=True),
        )


@router.callback_query(AddOpFSM.category, F.data == "fcat_new")
async def cat_new_start(cb: CallbackQuery, state: FSMContext) -> None:
    await _answer_cb(cb)
    await cb.message.answer(
        "Введите название категории 🏷️\n\n"
        "Например: Ремонт / Реклама / Аренда",
        reply_markup=_cancel_inline(),
    )
    await state.set_state(AddOpFSM.category_new)


@router.callback_query(AddOpFSM.category, F.data.startswith("fcat:"))
async def cat_choose(cb: CallbackQuery, state: FSMContext) -> None:
    await _answer_cb(cb)
    _, raw_id = str(cb.data).split(":", 1)
    if raw_id == "0":
        await state.update_data(category_id=None, category_name="")
        await _ask_comment(cb.message, state)
        return
    cid = int(raw_id)
    data = await state.get_data()
    op_type = str(data.get("op_type") or "expense")
    cats = await _get_cats(op_type)
    cat = next((c for c in cats if int(c.id) == cid), None)
    if cat is None:
        await cb.message.answer("Категория не найдена. Выберите другую.")
        return
    await state.update_data(category_id=int(cat.id), category_name=str(cat.name))
    await _ask_comment(cb.message, state)


@router.message(AddOpFSM.category_new)
async def cat_new_input(msg: Message, state: FSMContext) -> None:
    text = (msg.text or "").strip()
    if text in {_CANCEL_TEXT, "/cancel"}:
        await cancel_any(msg, state)
        return
    if not text:
        await msg.answer(
            "Название не может быть пустым. Введите название:",
            reply_markup=_cancel_inline(),
        )
        return
    data = await state.get_data()
    op_type = str(data.get("op_type") or "expense")
    try:
        async with get_async_session() as s:
            cat = await _create_cat(session=s, type=op_type, name=text)
        _invalidate_cat_cache()
        await state.update_data(category_id=int(cat.id), category_name=str(cat.name))
        await msg.answer(f"Категория «{cat.name}» создана ✅")
        await _ask_comment(msg, state)
    except Exception:
        log.exception("failed to create category")
        await msg.answer(
            "Не удалось создать категорию. Попробуйте другое название.",
            reply_markup=_cancel_inline(),
        )


# ── Step: comment ─────────────────────────────────────────────────────────────
@router.callback_query(AddOpFSM.comment, F.data == "fin:skip_cmt")
async def comment_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await _answer_cb(cb)
    await state.update_data(comment=None)
    await _ask_attachment(cb.message, state)


@router.message(AddOpFSM.comment)
async def step_comment(msg: Message, state: FSMContext) -> None:
    text = (msg.text or "").strip()
    if text in {_CANCEL_TEXT, "/cancel"}:
        await cancel_any(msg, state)
        return
    await state.update_data(comment=text or None)
    await _ask_attachment(msg, state)


# ── Step: attachment ──────────────────────────────────────────────────────────
@router.callback_query(AddOpFSM.attachment, F.data == "fin:skip_att")
async def attachment_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await _answer_cb(cb)
    await _ask_confirm(cb.message, state)


@router.message(AddOpFSM.attachment, F.photo)
async def step_attachment_photo(msg: Message, state: FSMContext) -> None:
    files = list((await state.get_data()).get("tg_file_ids") or [])
    try:
        files.append(str(msg.photo[-1].file_id))
    except Exception:
        pass
    await state.update_data(tg_file_ids=files)
    await msg.answer(
        f"Фото добавлено ({len(files)}) 📎\n\n"
        "Отправьте ещё фото или нажмите «Пропустить»",
        reply_markup=_skip_cancel_inline("att"),
    )


@router.message(AddOpFSM.attachment)
async def step_attachment_text(msg: Message, state: FSMContext) -> None:
    text = (msg.text or "").strip()
    if text in {_CANCEL_TEXT, "/cancel"}:
        await cancel_any(msg, state)
        return
    await msg.answer(
        "Ожидаю фото чека 🧾\n\n"
        "Или нажмите «Пропустить»",
        reply_markup=_skip_cancel_inline("att"),
    )


# ── Step: confirm ─────────────────────────────────────────────────────────────
@router.callback_query(AddOpFSM.confirm, F.data == "fop_save")
async def confirm_save(cb: CallbackQuery, state: FSMContext) -> None:
    await _answer_cb(cb)
    data = await state.get_data()
    await state.clear()
    try:
        async with get_async_session() as s:
            op = await _create_op(
                session=s,
                type=str(data.get("op_type") or "expense"),
                amount=Decimal(str(data.get("amount") or "0")),
                occurred_at=_utc_now(),
                category_id=data.get("category_id"),
                comment=data.get("comment"),
                tg_file_ids=list(data.get("tg_file_ids") or []),
            )
        op_type = str(data.get("op_type") or "expense")
        emoji = "➕" if op_type == "income" else "➖"
        label = "Доход" if op_type == "income" else "Расход"
        await cb.message.answer(
            f"✅ <b>Операция добавлена</b>\n\n"
            f"{emoji} {label}: <b>{_fmt_money(data.get('amount'))}</b>\n"
            f"📂 {data.get('category_name') or 'Без категории'}\n"
            f"📅 {_fmt_dt(op.occurred_at)}",
            reply_markup=_menu_kb(),
        )
    except Exception:
        log.exception("failed to save operation")
        await cb.message.answer(
            "Не удалось сохранить операцию. Попробуйте ещё раз.",
            reply_markup=_menu_kb(),
        )


# ── Summary & recent ops ──────────────────────────────────────────────────────
@router.message(F.text == "📊 Сводка за сегодня")
async def summary_today(msg: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _guard(msg, state):
        return
    now = _utc_now()
    date_from = now.replace(hour=0, minute=0, second=0, microsecond=0)
    await _send_summary(msg, date_from, now, "за сегодня")


@router.message(F.text == "📊 Сводка за месяц")
async def summary_month(msg: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _guard(msg, state):
        return
    now = _utc_now()
    date_from = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    await _send_summary(msg, date_from, now, "за месяц")


@router.message(F.text == "🧾 Последние операции")
async def recent_ops(msg: Message, state: FSMContext) -> None:
    await state.clear()
    if not await _guard(msg, state):
        return
    try:
        async with get_async_session() as s:
            rows, _ = await _list_ops(session=s, limit=5, offset=0)
        if not rows:
            await msg.answer("Пока операций нет.", reply_markup=_menu_kb())
            return
        lines = ["🧾 <b>Последние операции</b>\n"]
        for r in rows:
            emoji = "➕" if str(r.get("type")) == "income" else "➖"
            lines.append(
                f"{emoji} {_fmt_money(r.get('amount'))} · "
                f"{r.get('category_name') or 'Без категории'} · "
                f"{_fmt_dt(_parse_iso(str(r.get('occurred_at') or '')))}"
            )
        await msg.answer("\n".join(lines), reply_markup=_menu_kb())
    except Exception:
        log.exception("recent operations failed")
        await msg.answer("Не удалось загрузить последние операции.", reply_markup=_menu_kb())


# ── Backward compat: old keyboard button ─────────────────────────────────────
@router.message(F.text == "⚙️ Категории")
async def categories_compat(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("Главное меню:", reply_markup=_menu_kb())


# ── Helpers ───────────────────────────────────────────────────────────────────
def _parse_iso(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


async def _send_summary(msg: Message, date_from: datetime, date_to: datetime, period_label: str) -> None:
    try:
        async with get_async_session() as s:
            d = await _get_dash(session=s, date_from=date_from, date_to=date_to)
        profit = float(d.profit)
        sign = "+" if profit >= 0 else ""
        lines = []
        for c in d.top_expense_categories[:3]:
            lines.append(f"  • {c['category_name']}: {_fmt_money(c['total'])}")
        top = "\n".join(lines) if lines else "  Нет данных"
        await msg.answer(
            f"📊 <b>Сводка {period_label}</b>\n\n"
            f"Доходы: <b>{_fmt_money(d.income)}</b>\n"
            f"Расходы: <b>{_fmt_money(d.expense)}</b>\n"
            f"Баланс: <b>{sign}{_fmt_money(d.profit)}</b>\n\n"
            f"Топ-3 расходов:\n{top}",
            reply_markup=_menu_kb(),
        )
    except Exception:
        log.exception("summary failed")
        await msg.answer("Не удалось загрузить сводку. Попробуйте позже.", reply_markup=_menu_kb())
