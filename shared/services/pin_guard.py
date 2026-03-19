"""Shared PIN fail-counter helpers (module-scoped, in-memory)."""
from __future__ import annotations

import time
from typing import Dict, Tuple

_pin_fail_cache: Dict[str, Tuple[int, float]] = {}
_PIN_FAIL_WINDOW = 15 * 60  # 15 min sliding window
PIN_FAIL_MAX = 3


def record_pin_fail(key: str) -> int:
    """Record a failed attempt. Returns updated count."""
    now = time.time()
    count, last_ts = _pin_fail_cache.get(key, (0, now))
    if now - last_ts > _PIN_FAIL_WINDOW:
        count = 0
    count += 1
    _pin_fail_cache[key] = (count, now)
    return count


def clear_pin_fail(key: str) -> None:
    _pin_fail_cache.pop(key, None)


def should_alert(count: int) -> bool:
    return count >= PIN_FAIL_MAX
