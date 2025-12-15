from datetime import date, datetime
from typing import Optional, Union

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
