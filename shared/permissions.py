from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shared.enums import UserStatus, Position

if TYPE_CHECKING:
    from shared.models import User
    from shared.models import Task


@dataclass(frozen=True)
class UserRoleFlags:
    is_admin: bool
    is_manager: bool
    is_master: bool
    is_designer: bool


def role_flags(*, tg_id: int, admin_ids: list[int], status: UserStatus | None, position: Position | None) -> UserRoleFlags:
    is_admin = tg_id in admin_ids
    is_manager = status == UserStatus.APPROVED and position == Position.MANAGER
    is_master = status == UserStatus.APPROVED and position == Position.MASTER
    is_designer = status == UserStatus.APPROVED and position == Position.DESIGNER
    return UserRoleFlags(is_admin=is_admin, is_manager=is_manager, is_master=is_master, is_designer=is_designer)


def is_admin_or_manager(user: User | None = None, *, r: UserRoleFlags | None = None) -> bool:
    if r is None:
        if user is None:
            return False
        r = role_flags(tg_id=int(user.tg_id), admin_ids=[], status=user.status, position=user.position)
    return bool(getattr(r, "is_admin", False) or getattr(r, "is_manager", False))


def is_designer(user: User | None = None, *, r: UserRoleFlags | None = None) -> bool:
    if r is None:
        if user is None:
            return False
        r = role_flags(tg_id=int(user.tg_id), admin_ids=[], status=user.status, position=user.position)
    return bool(getattr(r, "is_designer", False))


def can_access_tasks(*, r: UserRoleFlags) -> bool:
    # Designers are allowed to use Tasks as regular executors.
    return True


def can_use_tasks_archive(*, r: UserRoleFlags) -> bool:
    # Admin/manager override any designer restrictions.
    if getattr(r, "is_admin", False) or getattr(r, "is_manager", False):
        return True
    # Designer (non-admin/manager): forbidden.
    if getattr(r, "is_designer", False):
        return False
    return True


def can_view_task(*, actor: User, t: Task, r: UserRoleFlags) -> bool:
    if getattr(r, "is_admin", False) or getattr(r, "is_manager", False):
        return True

    assignees = list(getattr(t, "assignees", None) or [])
    is_assignee = any(int(getattr(u, "id", 0) or 0) == int(getattr(actor, "id", 0) or 0) for u in assignees)

    # Designers must only see tasks where they are explicitly assigned.
    if getattr(r, "is_designer", False):
        return bool(is_assignee)

    # For non-designer staff, viewing tasks is allowed (actions are still restricted by task_permissions).
    return True


def can_use_purchases(*, r: UserRoleFlags, status: UserStatus | None = None) -> bool:
    # Admin/manager override any designer restrictions.
    if getattr(r, "is_admin", False) or getattr(r, "is_manager", False):
        return True
    # Designer (non-admin/manager): forbidden.
    if getattr(r, "is_designer", False):
        return False
    # Previous business rule: purchases available to approved employees.
    return status == UserStatus.APPROVED


def can_access_purchases(*, r: UserRoleFlags, status: UserStatus | None = None) -> bool:
    return can_use_purchases(r=r, status=status)


def can_access_shifts(*, r: UserRoleFlags, status: UserStatus | None = None) -> bool:
    # Admin/manager override any designer restrictions.
    if getattr(r, "is_admin", False) or getattr(r, "is_manager", False):
        return True
    # Designer (non-admin/manager): forbidden.
    if getattr(r, "is_designer", False):
        return False
    # Previous business rule: shifts available to approved employees.
    return status == UserStatus.APPROVED


def can_access_stocks(*, r: UserRoleFlags) -> bool:
    # Admin/manager override any designer restrictions.
    if getattr(r, "is_admin", False) or getattr(r, "is_manager", False):
        return True
    if getattr(r, "is_designer", False):
        return False
    return bool(getattr(r, "is_master", False))


def can_access_reports_module(*, r: UserRoleFlags) -> bool:
    # Admin/manager override any designer restrictions.
    if getattr(r, "is_admin", False) or getattr(r, "is_manager", False):
        return True
    return False


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
