from __future__ import annotations

from datetime import time
from typing import Optional


def normalize_shift_times(*, kind: str, start_time: Optional[time], end_time: Optional[time]) -> tuple[Optional[time], Optional[time]]:
    k = str(kind or "").strip()
    if k != "work":
        return None, None
    if start_time is None or end_time is None:
        return start_time, end_time
    if end_time == start_time:
        raise ValueError("start_equals_end")
    if end_time < start_time:
        raise ValueError("end_before_start")
    return start_time, end_time


def calc_int_hours_from_times(*, start_time: Optional[time], end_time: Optional[time]) -> Optional[int]:
    if start_time is None or end_time is None:
        return None
    diff_minutes = (end_time.hour * 60 + end_time.minute) - (start_time.hour * 60 + start_time.minute)
    if diff_minutes <= 0:
        return None
    if diff_minutes % 60 != 0:
        return None
    return int(diff_minutes // 60)


def format_hours_from_times_int(*, start_time: Optional[time], end_time: Optional[time]) -> str:
    h = calc_int_hours_from_times(start_time=start_time, end_time=end_time)
    return str(h) if h is not None else "—"


def emergency_preset_times(*, hours: int) -> tuple[time, time]:
    if int(hours) == 8:
        return time(10, 0), time(18, 0)
    if int(hours) == 10:
        return time(10, 0), time(20, 0)
    if int(hours) == 12:
        return time(10, 0), time(22, 0)
    raise ValueError("invalid hours")


def is_shift_final_status(status: object, *, ended_at: object | None = None) -> bool:
    if ended_at is not None:
        return True
    s = str(status or "")
    return s in {"approved", "pending_approval", "closed", "rejected", "needs_rework"}


def is_shift_active_status(status: object, *, ended_at: object | None = None) -> bool:
    if ended_at is not None:
        return False
    s = str(status or "")
    return s == "started"
