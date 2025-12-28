from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union, Any
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_moscow(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(MOSCOW_TZ)
    except Exception:
        return dt


def format_moscow(dt: Optional[datetime], fmt: str = "%d.%m.%Y %H:%M") -> str:
    d = to_moscow(dt)
    if d is None:
        return ""
    try:
        return d.strftime(fmt)
    except Exception:
        return ""

def format_date(d: Optional[Union[date, datetime]]) -> str:
    """Return date/datetime formatted as DD.MM.YYYY or empty string if None.
    Accepts both date and datetime objects.
    """
    if d is None:
        return ""
    if isinstance(d, datetime):
        return d.strftime("%d.%m.%Y")
    if isinstance(d, date):
        return d.strftime("%d.%m.%Y")
    try:
        # Fallback: try to strftime if object behaves like date
        return d.strftime("%d.%m.%Y")  # type: ignore[attr-defined]
    except Exception:
        return ""


def format_number(
    value: Any,
    *,
    max_decimals: int = 2,
    decimal_sep: str = ",",
    thousands_sep: str = " ",
    none_as_zero: bool = True,
    none_str: str = "—",
) -> str:
    if value is None:
        return "0" if none_as_zero else none_str

    # Convert to Decimal safely (avoid scientific notation and float artifacts where possible)
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, bool):
        d = Decimal(int(value))
    elif isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        d = Decimal(str(value))
    else:
        try:
            d = Decimal(str(value))
        except Exception:
            return none_str

    quant = Decimal("1") if max_decimals <= 0 else Decimal("0." + ("0" * (max_decimals - 1)) + "1")
    d = d.quantize(quant, rounding=ROUND_HALF_UP)

    is_int = d == d.to_integral_value()
    if is_int:
        s = str(int(d))
    else:
        # 'f' format never produces scientific notation
        s = format(d, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")

    # Thousands grouping
    sign = ""
    if s.startswith("-"):
        sign = "-"
        s = s[1:]

    if "." in s:
        int_part, frac_part = s.split(".", 1)
    else:
        int_part, frac_part = s, ""

    if thousands_sep:
        rev = int_part[::-1]
        grouped = thousands_sep.join(rev[i : i + 3] for i in range(0, len(rev), 3))[::-1]
    else:
        grouped = int_part

    if frac_part:
        return f"{sign}{grouped}{decimal_sep}{frac_part}"
    return f"{sign}{grouped}"
