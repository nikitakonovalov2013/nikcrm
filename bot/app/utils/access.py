from __future__ import annotations

from shared.permissions import UserRoleFlags


def is_admin_or_manager(*, r: UserRoleFlags) -> bool:
    return bool(getattr(r, "is_admin", False) or getattr(r, "is_manager", False))
