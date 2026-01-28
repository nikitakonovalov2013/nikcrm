from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.config import settings
from shared.enums import TaskPriority, TaskStatus, TaskEventType, UserStatus
from shared.models import Task, TaskEvent, TaskComment, TaskCommentPhoto, User
from shared.permissions import role_flags
from shared.services.task_audit import diff_task_for_audit
from shared.services.task_permissions import task_permissions, validate_status_transition
from shared.utils import MOSCOW_TZ, utc_now


@dataclass(frozen=True)
class UpdateTaskAuditResult:
    changed: bool


def _assignees_snapshot(t: Task) -> list[dict]:
    assignees = list(getattr(t, "assignees", None) or [])
    out: list[dict] = []
    for u in assignees:
        fio = f"{(getattr(u, 'first_name', '') or '').strip()} {(getattr(u, 'last_name', '') or '').strip()}".strip()
        out.append({"id": int(getattr(u, "id", 0) or 0), "name": fio or f"#{int(getattr(u, 'id', 0) or 0)}"})
    return out


def _task_snapshot(t: Task) -> dict:
    return {
        "title": str(getattr(t, "title", "") or ""),
        "description": str(getattr(t, "description", "") or ""),
        "priority": (getattr(getattr(t, "priority", None), "value", None) or str(getattr(t, "priority", "") or "")),
        "due_at": getattr(t, "due_at", None),
        "status": (getattr(getattr(t, "status", None), "value", None) or str(getattr(t, "status", "") or "")),
        "assignees": _assignees_snapshot(t),
        "has_photo": bool(
            getattr(t, "photo_key", None)
            or getattr(t, "photo_path", None)
            or getattr(t, "photo_url", None)
            or getattr(t, "tg_photo_file_id", None)
            or getattr(t, "photo_file_id", None)
        ),
        "photo_key": getattr(t, "photo_key", None),
    }


def _parse_due_at_iso_or_msk(value: str | None) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MOSCOW_TZ)
    return dt.astimezone(timezone.utc)


async def _load_task_full(session: AsyncSession, task_id: int) -> Task:
    res = await session.execute(
        select(Task)
        .where(Task.id == int(task_id))
        .options(
            selectinload(Task.assignees),
            selectinload(Task.created_by_user),
            selectinload(Task.comments).selectinload(TaskComment.author_user),
            selectinload(Task.comments).selectinload(TaskComment.photos),
            selectinload(Task.events).selectinload(TaskEvent.actor_user),
        )
    )
    t = res.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404)
    return t


async def update_task_with_audit(
    *,
    session: AsyncSession,
    actor: User,
    task_id: int,
    patch: dict,
    photo_action: str | None = None,
) -> UpdateTaskAuditResult:
    t = await _load_task_full(session, int(task_id))

    r = role_flags(
        tg_id=int(getattr(actor, "tg_id", 0) or 0),
        admin_ids=settings.admin_ids,
        status=actor.status,
        position=actor.position,
    )
    if not (bool(r.is_admin) or bool(r.is_manager)):
        raise HTTPException(status_code=403, detail="Недостаточно прав для редактирования")

    before = _task_snapshot(t)

    if "title" in patch and patch.get("title") is not None:
        t.title = str(patch.get("title") or "").strip()

    if "description" in patch:
        desc_raw = patch.get("description")
        desc = str(desc_raw).strip() if desc_raw is not None else ""
        t.description = desc or None

    if "priority" in patch and patch.get("priority") is not None:
        p = str(patch.get("priority") or "").strip()
        if p == TaskPriority.URGENT.value:
            t.priority = TaskPriority.URGENT
        elif p == TaskPriority.FREE_TIME.value:
            t.priority = TaskPriority.FREE_TIME
        else:
            t.priority = TaskPriority.NORMAL

    if "due_at" in patch:
        due_raw = patch.get("due_at")
        t.due_at = _parse_due_at_iso_or_msk(str(due_raw) if due_raw is not None else None)

    if "assignee_ids" in patch and patch.get("assignee_ids") is not None:
        ids = [int(x) for x in (patch.get("assignee_ids") or []) if int(x) > 0]
        users: list[User] = []
        if ids:
            users = list(
                (
                    await session.scalars(
                        select(User)
                        .where(User.id.in_(ids))
                        .where(User.is_deleted == False)
                        .where(User.status == UserStatus.APPROVED)
                    )
                ).all()
            )
            if len(users) != len(set(ids)):
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Исполнители не найдены")
        t.assignees = users

    if "status" in patch and patch.get("status") is not None:
        new_status = str(patch.get("status") or "").strip()
        old_status = t.status.value if hasattr(t.status, "value") else str(t.status)
        perms = task_permissions(
            status=str(old_status),
            actor_user_id=int(actor.id),
            created_by_user_id=int(getattr(t, "created_by_user_id", 0) or 0) or None,
            assignee_user_ids=[int(u.id) for u in list(getattr(t, "assignees", None) or [])],
            started_by_user_id=(int(getattr(t, "started_by_user_id")) if getattr(t, "started_by_user_id", None) is not None else None),
            is_admin=bool(r.is_admin),
            is_manager=bool(r.is_manager),
        )
        ok, code, msg = validate_status_transition(
            from_status=str(old_status),
            to_status=str(new_status),
            perms=perms,
            comment=None,
        )
        if not ok:
            raise HTTPException(status_code=int(code), detail=str(msg or "Ошибка"))
        if new_status == TaskStatus.IN_PROGRESS.value:
            t.status = TaskStatus.IN_PROGRESS
        elif new_status == TaskStatus.REVIEW.value:
            t.status = TaskStatus.REVIEW
            t.completed_by_user_id = int(actor.id)
            t.completed_at = utc_now()
        elif new_status == TaskStatus.DONE.value:
            t.status = TaskStatus.DONE

    if "archived" in patch and patch.get("archived") is not None:
        want_archived = bool(patch.get("archived"))
        old_status = t.status.value if hasattr(t.status, "value") else str(t.status)
        perms = task_permissions(
            status=str(old_status),
            actor_user_id=int(actor.id),
            created_by_user_id=int(getattr(t, "created_by_user_id", 0) or 0) or None,
            assignee_user_ids=[int(u.id) for u in list(getattr(t, "assignees", None) or [])],
            started_by_user_id=(int(getattr(t, "started_by_user_id")) if getattr(t, "started_by_user_id", None) is not None else None),
            is_admin=bool(r.is_admin),
            is_manager=bool(r.is_manager),
        )
        if want_archived:
            if not perms.archive:
                raise HTTPException(status_code=403, detail="Недостаточно прав для архивирования")
            t.status = TaskStatus.ARCHIVED
            t.archived_at = utc_now()
        else:
            if old_status == TaskStatus.ARCHIVED.value:
                if not perms.unarchive:
                    raise HTTPException(status_code=403, detail="Недостаточно прав для разархивирования")
                t.status = TaskStatus.DONE
                t.archived_at = None

    if bool(patch.get("remove_photo")):
        t.photo_key = None
        t.photo_path = None
        t.photo_url = None
        try:
            t.tg_photo_file_id = None
        except Exception:
            pass
        try:
            t.photo_file_id = None
        except Exception:
            pass

    if "tg_photo_file_id" in patch and patch.get("tg_photo_file_id") is not None:
        val = str(patch.get("tg_photo_file_id") or "")
        try:
            t.tg_photo_file_id = val or None
        except Exception:
            try:
                t.photo_file_id = val or None
            except Exception:
                pass

    if "photo_key" in patch and patch.get("photo_key") is not None:
        t.photo_key = str(patch.get("photo_key") or "") or None
    if "photo_path" in patch and patch.get("photo_path") is not None:
        t.photo_path = str(patch.get("photo_path") or "") or None
    if "photo_url" in patch and patch.get("photo_url") is not None:
        t.photo_url = str(patch.get("photo_url") or "") or None

    await session.flush()

    after = _task_snapshot(t)
    if photo_action:
        after["photo_action"] = str(photo_action)

    changes, human = diff_task_for_audit(before=before, after=after)
    if not changes:
        return UpdateTaskAuditResult(changed=False)

    session.add(
        TaskEvent(
            task_id=int(t.id),
            actor_user_id=int(actor.id),
            type=TaskEventType.EDITED,
            payload={
                "changes": [
                    {"type": c.type, "field": c.field, "before": c.before, "after": c.after, "human": c.human}
                    for c in changes
                ],
                "human": list(human),
            },
        )
    )
    await session.flush()
    return UpdateTaskAuditResult(changed=True)
