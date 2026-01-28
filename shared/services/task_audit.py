from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from shared.enums import TaskPriority, TaskStatus
from shared.utils import format_moscow


PRIORITY_RU = {
    TaskPriority.URGENT.value: "Срочно",
    TaskPriority.NORMAL.value: "Обычный",
    TaskPriority.FREE_TIME.value: "В свободное время",
}

STATUS_RU = {
    TaskStatus.NEW.value: "Новая",
    TaskStatus.IN_PROGRESS.value: "В работе",
    TaskStatus.REVIEW.value: "На проверке",
    TaskStatus.DONE.value: "Выполнено",
    TaskStatus.ARCHIVED.value: "Архив",
}

@dataclass(frozen=True)
class FieldChange:
    type: str
    field: str
    before: object
    after: object
    human: str


def _q(s: str) -> str:
    return f"«{s}»"


def _to_utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _fmt_dt_msk(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return format_moscow(dt, "%d.%m.%Y %H:%M")


def diff_task_for_audit(*, before: dict, after: dict) -> tuple[list[FieldChange], list[str]]:
    changes: list[FieldChange] = []
    human: list[str] = []

    def add(field: str, before_v: object, after_v: object, human_line: str):
        ch = FieldChange(type="field", field=str(field), before=before_v, after=after_v, human=str(human_line))
        changes.append(ch)
        human.append(str(human_line))

    b_title = str(before.get("title") or "")
    a_title = str(after.get("title") or "")
    if b_title != a_title:
        add("title", b_title, a_title, f"Изменено название: {_q(b_title)} → {_q(a_title)}")

    b_desc = str(before.get("description") or "")
    a_desc = str(after.get("description") or "")
    if b_desc != a_desc:
        # do not expand long text in history
        add("description", None if not b_desc else "<text>", None if not a_desc else "<text>", "Изменено описание")

    b_pr = str(before.get("priority") or "")
    a_pr = str(after.get("priority") or "")
    if b_pr != a_pr:
        add(
            "priority",
            b_pr,
            a_pr,
            f"Изменён приоритет: {PRIORITY_RU.get(b_pr, b_pr)} → {PRIORITY_RU.get(a_pr, a_pr)}",
        )

    b_due = before.get("due_at")
    a_due = after.get("due_at")
    # compare iso to avoid tz jitter
    if _to_utc_iso(b_due) != _to_utc_iso(a_due):
        add("due_at", _to_utc_iso(b_due), _to_utc_iso(a_due), f"Изменён дедлайн: {_fmt_dt_msk(b_due)} → {_fmt_dt_msk(a_due)}")

    b_status = str(before.get("status") or "")
    a_status = str(after.get("status") or "")
    if b_status != a_status:
        add(
            "status",
            b_status,
            a_status,
            f"Изменён статус: {STATUS_RU.get(b_status, b_status)} → {STATUS_RU.get(a_status, a_status)}",
        )

    b_ass = list(before.get("assignees") or [])
    a_ass = list(after.get("assignees") or [])
    b_ass_ids = [int(x.get("id")) for x in b_ass if isinstance(x, dict) and x.get("id") is not None]
    a_ass_ids = [int(x.get("id")) for x in a_ass if isinstance(x, dict) and x.get("id") is not None]
    if b_ass_ids != a_ass_ids:
        b_names = ", ".join([str(x.get("name") or "") for x in b_ass]) or "—"
        a_names = ", ".join([str(x.get("name") or "") for x in a_ass]) or "—"
        add("assignees", b_ass_ids, a_ass_ids, f"Изменены исполнители: {b_names} → {a_names}")

    b_photo = bool(before.get("has_photo"))
    a_photo = bool(after.get("has_photo"))
    if b_photo != a_photo:
        if not b_photo and a_photo:
            add("photo", False, True, "Фото: добавлено")
        elif b_photo and not a_photo:
            add("photo", True, False, "Фото: удалено")

    # explicit action from photo endpoints
    b_photo_action = str(after.get("photo_action") or "").strip()
    if b_photo_action in {"added", "replaced", "removed"}:
        ru = {"added": "Фото: добавлено", "replaced": "Фото: заменено", "removed": "Фото: удалено"}[b_photo_action]
        # only add if not already present
        if ru not in human:
            add("photo", before.get("photo_key"), after.get("photo_key"), ru)

    return changes, human
