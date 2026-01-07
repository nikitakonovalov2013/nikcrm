from __future__ import annotations

from dataclasses import dataclass

from shared.enums import TaskStatus


@dataclass(frozen=True)
class TaskPermissions:
    take_in_progress: bool
    finish_to_review: bool
    accept_done: bool
    send_back: bool
    archive: bool
    unarchive: bool
    comment: bool


def task_permissions(
    *,
    status: str,
    actor_user_id: int,
    created_by_user_id: int | None,
    assignee_user_ids: list[int],
    started_by_user_id: int | None,
    is_admin: bool,
    is_manager: bool,
) -> TaskPermissions:
    actor_user_id = int(actor_user_id)
    assignee_user_ids = [int(x) for x in (assignee_user_ids or [])]

    is_assigned = actor_user_id in assignee_user_ids
    is_common = len(assignee_user_ids) == 0
    is_started_by_me = bool(started_by_user_id is not None and int(started_by_user_id) == actor_user_id)
    is_creator = bool(created_by_user_id is not None and int(created_by_user_id) == actor_user_id)

    is_admin_or_manager = bool(is_admin or is_manager)

    can_take = status == TaskStatus.NEW.value and (is_admin_or_manager or is_assigned or is_common)

    can_finish = status == TaskStatus.IN_PROGRESS.value and (is_assigned or (is_common and is_started_by_me))

    can_accept = status == TaskStatus.REVIEW.value and is_admin_or_manager

    can_send_back = status == TaskStatus.REVIEW.value and is_admin_or_manager

    can_archive = status in {
        TaskStatus.NEW.value,
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.REVIEW.value,
        TaskStatus.DONE.value,
    } and is_admin_or_manager

    can_unarchive = status == TaskStatus.ARCHIVED.value and is_admin_or_manager

    can_comment = is_admin_or_manager or is_assigned or is_started_by_me or is_common or is_creator

    return TaskPermissions(
        take_in_progress=bool(can_take),
        finish_to_review=bool(can_finish),
        accept_done=bool(can_accept),
        send_back=bool(can_send_back),
        archive=bool(can_archive),
        unarchive=bool(can_unarchive),
        comment=bool(can_comment),
    )


def validate_status_transition(
    *,
    from_status: str,
    to_status: str,
    perms: TaskPermissions,
    comment: str | None,
) -> tuple[bool, int, str]:
    to_status = (to_status or "").strip()

    if to_status not in {
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.REVIEW.value,
        TaskStatus.DONE.value,
    }:
        return False, 400, "Неподдерживаемый статус"

    if to_status == TaskStatus.IN_PROGRESS.value:
        if not (perms.take_in_progress or perms.send_back):
            return False, 403, "Недостаточно прав для перевода в «В работе»"
        if from_status == TaskStatus.REVIEW.value and perms.send_back:
            if not (comment or "").strip():
                return False, 400, "Комментарий обязателен для «На доработку»"
        return True, 200, ""

    if to_status == TaskStatus.REVIEW.value:
        if not perms.finish_to_review:
            return False, 403, "Недостаточно прав для перевода в «На проверке»"
        return True, 200, ""

    if to_status == TaskStatus.DONE.value:
        if not perms.accept_done:
            return False, 403, "Недостаточно прав для перевода в «Выполнено»"
        return True, 200, ""

    return False, 400, "Неподдерживаемый переход"
