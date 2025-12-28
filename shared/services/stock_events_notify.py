from __future__ import annotations

import logging
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
    if kind_norm in {"consumption", "out", "расход"}:
        header = "➖ <b>Расход</b>"
    elif kind_norm in {"supply", "in", "пополнение"}:
        header = "➕ <b>Пополнение</b>"
    else:
        header = "📦 <b>Операция по складу</b>"

    dt_str = format_moscow(happened_at or utc_now())

    amount_str = format_number(amount, max_decimals=3, decimal_sep=".", thousands_sep=" ")

    lines: list[str] = [
        header,
        "",
        f"📦 <b>Материал:</b> {material_name or '—'}",
        f"🔢 <b>Кол-во:</b> {amount_str} {unit or ''}",
        f"👤 <b>Кто:</b> {_fmt_actor(actor)}",
        f"⏱ <b>Когда:</b> {dt_str}",
    ]

    if stock_after is not None:
        stock_str = format_number(stock_after, max_decimals=3, decimal_sep=".", thousands_sep=" ")
        lines.append(f"📊 <b>Остаток сейчас:</b> {stock_str} {unit or ''}")

    text = "\n".join(lines)

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
