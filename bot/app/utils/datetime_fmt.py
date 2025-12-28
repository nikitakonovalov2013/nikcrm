from __future__ import annotations

from datetime import date, datetime

from shared.utils import format_date, format_moscow


def format_date_ru(d: date | datetime | None) -> str:
    return format_date(d)


def format_dt_ru(dt: datetime | None) -> str:
    return format_moscow(dt)
