from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import User


USER_COLOR_PALETTE: list[str] = [
    "#EF4444",
    "#F97316",
    "#F59E0B",
    "#84CC16",
    "#22C55E",
    "#10B981",
    "#14B8A6",
    "#06B6D4",
    "#0EA5E9",
    "#3B82F6",
    "#6366F1",
    "#8B5CF6",
    "#A855F7",
    "#D946EF",
    "#EC4899",
    "#F43F5E",
    "#64748B",
    "#0F766E",
    "#B45309",
    "#4D7C0F",
]


def _norm_hex(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    if len(v) != 7 or not v.startswith("#"):
        return None
    ok = all(ch in "0123456789abcdefABCDEF" for ch in v[1:])
    return v.upper() if ok else None


def _hash_to_palette_index(seed: int, n: int) -> int:
    # Deterministic simple hash; n > 0
    x = int(seed) & 0xFFFFFFFF
    x ^= (x >> 16)
    x = (x * 0x7FEB352D) & 0xFFFFFFFF
    x ^= (x >> 15)
    x = (x * 0x846CA68B) & 0xFFFFFFFF
    x ^= (x >> 16)
    return int(x % n)


async def assign_user_color(session: AsyncSession, *, seed: int | None = None) -> str:
    """Pick a default user color.

    - Prefer unused colors from USER_COLOR_PALETTE.
    - If all are used, pick a deterministic palette color based on seed.
    """

    res = await session.execute(select(User.color).where(User.color.is_not(None)))
    used = {_norm_hex(r[0]) for r in res.all()}
    used.discard(None)

    for c in USER_COLOR_PALETTE:
        if _norm_hex(c) not in used:
            return c

    n = len(USER_COLOR_PALETTE)
    if n <= 0:
        return "#64748B"

    idx = 0
    if seed is not None:
        idx = _hash_to_palette_index(int(seed), n)
    return USER_COLOR_PALETTE[idx]
