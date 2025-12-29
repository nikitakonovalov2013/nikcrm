from __future__ import annotations

from dataclasses import dataclass

from shared.enums import UserStatus, Position


@dataclass(frozen=True)
class UserRoleFlags:
    is_admin: bool
    is_manager: bool
    is_master: bool


def role_flags(*, tg_id: int, admin_ids: list[int], status: UserStatus | None, position: Position | None) -> UserRoleFlags:
    is_admin = tg_id in admin_ids
    is_manager = status == UserStatus.APPROVED and position == Position.MANAGER
    is_master = status == UserStatus.APPROVED and position == Position.MASTER
    return UserRoleFlags(is_admin=is_admin, is_manager=is_manager, is_master=is_master)


def can_access_web_panel(*, tg_id: int, admin_ids: list[int], status: UserStatus | None, position: Position | None) -> bool:
    r = role_flags(tg_id=tg_id, admin_ids=admin_ids, status=status, position=position)
    return r.is_admin or r.is_manager


def can_view_stocks(*, tg_id: int, admin_ids: list[int], status: UserStatus | None, position: Position | None) -> bool:
    # Production policy for Stocks: view for admin + manager + master.
    r = role_flags(tg_id=tg_id, admin_ids=admin_ids, status=status, position=position)
    return r.is_admin or r.is_manager or r.is_master


def can_manage_stock_ops(*, tg_id: int, admin_ids: list[int], status: UserStatus | None, position: Position | None) -> bool:
    # расход/пополнение: only admin + manager
    r = role_flags(tg_id=tg_id, admin_ids=admin_ids, status=status, position=position)
    return r.is_admin or r.is_manager


def can_manage_stock_op(
    *,
    tg_id: int,
    admin_ids: list[int],
    status: UserStatus | None,
    position: Position | None,
    op: str,
) -> bool:
    r = role_flags(tg_id=tg_id, admin_ids=admin_ids, status=status, position=position)
    if r.is_admin or r.is_manager:
        return op in {"in", "out"}
    if r.is_master:
        return op == "out"
    return False


def can_access_reports(*, tg_id: int, admin_ids: list[int], status: UserStatus | None, position: Position | None) -> bool:
    # reports/reminders management: only admin + manager
    r = role_flags(tg_id=tg_id, admin_ids=admin_ids, status=status, position=position)
    return r.is_admin or r.is_manager
