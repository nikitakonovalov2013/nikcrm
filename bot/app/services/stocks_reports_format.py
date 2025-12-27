from __future__ import annotations

from bot.app.utils.datetime_fmt import format_dt_ru, format_date_ru
from bot.app.services.stocks_reports import ReportData
from shared.utils import format_number


def format_report_html(title: str, data: ReportData) -> str:
    lines: list[str] = []
    lines.append(f"📊 <b>{title}</b>")
    lines.append(f"🗓 Период: <b>{format_dt_ru(data.start)}</b> — <b>{format_dt_ru(data.end)}</b>")
    lines.append("")
    lines.append("<b>Сводка</b>")
    lines.append(f"➕ Приход: <b>{format_number(data.total_in)}</b>")
    lines.append(f"➖ Расход: <b>{format_number(data.total_out)}</b>")
    if data.top_out:
        lines.append(
            f"🔥 Топ по расходу: <b>{data.top_out.name}</b> — {format_number(data.top_out.outgoing)} {data.top_out.unit}"
        )
    lines.append("")

    if data.materials:
        lines.append("<b>По материалам</b>")
        for m in data.materials:
            net = m.incoming - m.outgoing
            lines.append(
                f"• <b>{m.name}</b>: ➕ {format_number(m.incoming)} {m.unit} | ➖ {format_number(m.outgoing)} {m.unit} | Δ {format_number(net)} {m.unit}"
            )
        lines.append("")

    lines.append("<b>Последние события</b>")
    if not data.events:
        lines.append("—")
    else:
        for e in data.events:
            sign = "➕" if e.kind == "in" else "➖"
            lines.append(
                f"{sign} {format_dt_ru(e.dt)} — <b>{e.user_fio}</b>: {e.material_name} {format_number(e.amount)} {e.unit}"
            )

    return "\n".join(lines)
