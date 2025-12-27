from __future__ import annotations

from datetime import date, datetime


def format_date_ru(d: date | datetime | None) -> str:
    if d is None:
        return ""
    if isinstance(d, datetime):
        return d.strftime("%d.%m.%Y")
    return d.strftime("%d.%m.%Y")


def format_dt_ru(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%d.%m.%Y %H:%M")
