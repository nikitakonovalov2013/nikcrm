from __future__ import annotations

from bot.app.utils.datetime_fmt import format_dt_ru
from bot.app.services.stocks_reports import ReportData
from shared.utils import format_number


RUB_PER_KG = 530


def _ddmm(dt) -> str:
    try:
        return dt.strftime("%d.%m")
    except Exception:
        return "—"


def format_report_html(title: str, data: ReportData) -> str:
    # New strict compact format
    lines: list[str] = []

    kg_out = data.total_out
    kg_in = data.total_in

    lines.append(f"📊 <b>Отчет за {_ddmm(data.start)}</b>")

    lines.append(f"— Расход: {format_number(kg_out)} кг")
    k_int = int(kg_out)
    rub = k_int * RUB_PER_KG
    lines.append(f"💰 В сумме: {rub} руб")
    warehouse_price_s = format_number(getattr(data, "warehouse_price_rub", 0), max_decimals=0)
    lines.append(f"💵 Цена склада: {warehouse_price_s} руб")
    lines.append(f"+ Приход: {format_number(kg_in)} кг")

    lines.append("")
    lines.append("👥 <b>По работникам</b>")
    if not getattr(data, "outgoing_by_user", None):
        lines.append("Нет данных.")
    else:
        for u in data.outgoing_by_user:
            lines.append(f"{u.fio}: {format_number(u.outgoing)} кг")

    lines.append("")
    lines.append("📦 <b>По материалам</b>")
    if not data.materials:
        lines.append("Нет данных.")
    else:
        mats_sorted = sorted(
            data.materials,
            key=lambda m: (-m.outgoing, str(m.name).lower()),
        )
        for m in mats_sorted:
            net = m.incoming - m.outgoing
            lines.append(
                f"{m.name}: +{format_number(m.incoming)} {m.unit} | -{format_number(m.outgoing)} {m.unit} | Δ{format_number(net)} {m.unit}"
            )

    return "\n".join(lines)
