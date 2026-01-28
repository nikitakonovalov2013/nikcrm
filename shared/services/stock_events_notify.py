from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties

from shared.config import settings
from shared.utils import format_number, format_moscow, utc_now

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StockEventActor:
    name: str
    tg_id: int | None


def _fmt_actor(actor: StockEventActor | None) -> str:
    if not actor:
        return "—"
    if actor.tg_id:
        return f"{actor.name} (TG: <code>{actor.tg_id}</code>)"
    return actor.name or "—"


def _fmt_actor_name_only(actor: StockEventActor | None) -> str:
    if not actor:
        return "—"
    return actor.name or "—"


_PARENS_RE = re.compile(r"\s*\([^)]*\)")


def strip_parentheses_suffix(text: str | None) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = _PARENS_RE.sub("", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


async def notify_reports_chat_about_stock_event(
    *,
    kind: str,
    material_name: str,
    amount: Decimal,
    unit: str,
    actor: StockEventActor | None,
    happened_at: datetime | None = None,
    stock_after: Decimal | None = None,
) -> None:
    chat_id = int(getattr(settings, "REPORTS_CHAT_ID", 0) or 0)
    if not chat_id:
        return

    kind_norm = (kind or "").lower()
    dt_str = format_moscow(happened_at or utc_now())
    amount_str = format_number(amount, max_decimals=3, decimal_sep=".", thousands_sep=" ")

    # Expense: new compact format (do not show TG id, no headers)
    if kind_norm in {"consumption", "out", "расход"}:
        actor_str = _fmt_actor_name_only(actor)
        material_str = strip_parentheses_suffix(material_name) or "—"
        kg_unit = "кг"
        lines: list[str] = [
            f"{actor_str}: — Добавлен расход: {amount_str} {kg_unit} {material_str}",
            "",
        ]
        if stock_after is not None:
            stock_str = format_number(stock_after, max_decimals=3, decimal_sep=".", thousands_sep=" ")
            lines.append(f"📊 Остаток сейчас: {stock_str} {kg_unit}")
        lines.append(f"🕒 Когда: {dt_str}")
        text = "\n".join(lines)
    else:
        # Supply/other: keep existing format
        if kind_norm in {"supply", "in", "пополнение"}:
            header = "➕ <b>Пополнение</b>"
        else:
            header = "📦 <b>Операция по складу</b>"

        lines2: list[str] = [
            header,
            "",
            f"📦 <b>Материал:</b> {material_name or '—'}",
            f"🔢 <b>Кол-во:</b> {amount_str} {unit or ''}",
            f"👤 <b>Кто:</b> {_fmt_actor(actor)}",
            f"⏱ <b>Когда:</b> {dt_str}",
        ]

        if stock_after is not None:
            stock_str = format_number(stock_after, max_decimals=3, decimal_sep=".", thousands_sep=" ")
            lines2.append(f"📊 <b>Остаток сейчас:</b> {stock_str} {unit or ''}")

        text = "\n".join(lines2)

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception:
        _logger.exception(
            "failed to notify reports chat about stock event",
            extra={"chat_id": chat_id, "kind": kind, "material": material_name},
        )
    finally:
        await bot.session.close()
