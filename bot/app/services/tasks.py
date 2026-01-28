from __future__ import annotations

from shared.config import settings
from shared.enums import TaskEventType, TaskPriority, TaskStatus
from shared.permissions import role_flags
from shared.services.task_permissions import TaskPermissions, task_permissions, validate_status_transition
from shared.utils import format_moscow, utc_now
from shared.models import TaskComment, TaskEvent
from shared.services.task_notifications import TaskNotificationService

from bot.app.repository.tasks import TaskRepository
from bot.app.utils.html import esc


def _task_permissions(*, task, actor, is_admin: bool, is_manager: bool) -> TaskPermissions:
    assignees = list(getattr(task, "assignees", None) or [])
    st = task.status.value if hasattr(task.status, "value") else str(task.status)
    return task_permissions(
        status=str(st),
        actor_user_id=int(actor.id),
        created_by_user_id=int(getattr(task, "created_by_user_id", 0) or 0) or None,
        assignee_user_ids=[int(u.id) for u in assignees],
        started_by_user_id=(int(getattr(task, "started_by_user_id")) if getattr(task, "started_by_user_id", None) is not None else None),
        is_admin=bool(is_admin),
        is_manager=bool(is_manager),
    )


def _status_human(st: str) -> str:
    return {
        TaskStatus.NEW.value: "Новая",
        TaskStatus.IN_PROGRESS.value: "В работе",
        TaskStatus.REVIEW.value: "На проверке",
        TaskStatus.DONE.value: "Выполнено",
        TaskStatus.ARCHIVED.value: "Архив",
    }.get(st, st)


def _priority_human(p: str) -> str:
    if p == TaskPriority.URGENT.value:
        return "🔥 Срочная"
    if p == TaskPriority.FREE_TIME.value:
        return "В свободное время"
    return "Обычная"


def _user_name(u) -> str:
    if not u:
        return "—"
    fio = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip()
    return fio or f"#{int(u.id)}"


def _elapsed_hm(created_at) -> str:
    if not created_at:
        return "—"
    try:
        now = utc_now()
        dt = created_at
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=now.tzinfo)
        sec = int((now - dt).total_seconds())
        if sec < 0:
            sec = 0
        h = sec // 3600
        m = (sec % 3600) // 60
        return f"{int(h)} ч {int(m):02d} мин"
    except Exception:
        return "—"


class TasksService:
    def __init__(self, repo: TaskRepository):
        self.repo = repo

    async def get_actor_or_none(self, tg_id: int):
        actor = await self.repo.get_user_by_tg_id_any(int(tg_id))
        if not actor:
            return None
        if bool(getattr(actor, "is_deleted", False)):
            return None
        try:
            from shared.enums import UserStatus

            if actor.status in (UserStatus.BLACKLISTED, UserStatus.PENDING, UserStatus.REJECTED):
                return None
            if actor.status != UserStatus.APPROVED:
                return None
        except Exception:
            # if enums mismatch for any reason, be conservative
            return None
        return actor

    async def list_for_actor(
        self,
        *,
        tg_id: int,
        kind: str,
        page: int,
        limit: int,
    ):
        actor = await self.get_actor_or_none(tg_id)
        if not actor:
            return None, [], False, False

        r = role_flags(
            tg_id=tg_id,
            admin_ids=settings.admin_ids,
            status=actor.status,
            position=actor.position,
        )
        is_admin_or_manager = bool(r.is_admin or r.is_manager)

        tasks, has_prev, has_next = await self.repo.list_tasks(
            kind=kind,
            actor_user_id=int(actor.id),
            is_admin_or_manager=is_admin_or_manager,
            page=page,
            limit=limit,
        )
        return actor, tasks, has_prev, has_next

    async def get_detail(self, *, tg_id: int, task_id: int):
        actor = await self.get_actor_or_none(tg_id)
        if not actor:
            return None, None, None

        task = await self.repo.get_task_full(task_id)
        if not task:
            return actor, None, None

        r = role_flags(
            tg_id=tg_id,
            admin_ids=settings.admin_ids,
            status=actor.status,
            position=actor.position,
        )
        perms = _task_permissions(task=task, actor=actor, is_admin=r.is_admin, is_manager=r.is_manager)
        return actor, task, perms

    async def add_comment(
        self,
        *,
        tg_id: int,
        task_id: int,
        text: str | None,
        photo_file_ids: list[str],
    ) -> bool:
        actor = await self.get_actor_or_none(tg_id)
        if not actor:
            return False
        task = await self.repo.get_task_full(task_id)
        if not task:
            return False

        c = await self.repo.add_comment(
            task_id=task_id,
            author_user_id=int(actor.id),
            text=text or None,
            photo_file_ids=photo_file_ids,
        )

        self.repo.session.add(
            TaskEvent(
                task_id=int(task_id),
                actor_user_id=int(actor.id),
                type=TaskEventType.COMMENT_ADDED,
                payload={"has_text": bool(text and text.strip()), "photos_count": int(len(photo_file_ids))},
            )
        )
        await self.repo.session.flush()

        try:
            # Notify the other side
            assignees = list(getattr(task, "assignees", None) or [])
            is_executor = any(int(u.id) == int(actor.id) for u in assignees) or (
                (len(assignees) == 0) and (getattr(task, "started_by_user_id", None) is not None) and int(getattr(task, "started_by_user_id")) == int(actor.id)
            )

            recipients: list[int] = []
            if is_executor:
                recipients = [int(getattr(task, "created_by_user_id"))]
            else:
                if assignees:
                    recipients = [int(u.id) for u in assignees]
                else:
                    sb = getattr(task, "started_by_user_id", None)
                    if sb is not None:
                        recipients = [int(sb)]

            recipients = [r for r in recipients if int(r) != int(actor.id)]
            if recipients:
                ns = TaskNotificationService(self.repo.session)
                actor_name = _user_name(actor)
                for rid in recipients:
                    await ns.enqueue(
                        task_id=int(task.id),
                        recipient_user_id=int(rid),
                        type="comment",
                        payload={
                            "task_id": int(task.id),
                            "comment_id": int(getattr(c, "id", 0) or 0),
                            "text": (text or ""),
                            "photos_count": int(len(photo_file_ids)),
                            "actor_user_id": int(actor.id),
                            "actor_name": actor_name,
                        },
                        dedupe_key=f"comment:{int(getattr(c, 'id', 0) or 0)}",
                    )
        except Exception:
            # notifications must not break core flow
            pass
        return True

    async def change_status(
        self,
        *,
        tg_id: int,
        task_id: int,
        to_status: str,
        comment: str | None = None,
    ) -> tuple[bool, str]:
        actor = await self.get_actor_or_none(tg_id)
        if not actor:
            return False, "not_registered"

        task = await self.repo.get_task_full(task_id)
        if not task:
            return False, "not_found"

        r = role_flags(
            tg_id=tg_id,
            admin_ids=settings.admin_ids,
            status=actor.status,
            position=actor.position,
        )
        perms = _task_permissions(task=task, actor=actor, is_admin=r.is_admin, is_manager=r.is_manager)

        old_status = task.status.value if hasattr(task.status, "value") else str(task.status)
        comment_str = (comment or "").strip()

        ok, code, _msg = validate_status_transition(
            from_status=str(old_status),
            to_status=str(to_status),
            perms=perms,
            comment=comment_str,
        )
        if not ok:
            if code == 403:
                return False, "forbidden"
            if code == 400 and "Комментарий" in (_msg or ""):
                return False, "comment_required"
            return False, "unsupported"

        if to_status == TaskStatus.IN_PROGRESS.value:
            task.status = TaskStatus.IN_PROGRESS
            assignees = list(getattr(task, "assignees", None) or [])
            if len(assignees) == 0:
                task.started_by_user_id = int(actor.id)
                task.started_at = utc_now()
            if old_status == TaskStatus.REVIEW.value and perms.send_back:
                self.repo.session.add(TaskComment(task_id=int(task.id), author_user_id=int(actor.id), text=comment_str))
        elif to_status == TaskStatus.REVIEW.value:
            task.status = TaskStatus.REVIEW
            task.completed_by_user_id = int(actor.id)
            task.completed_at = utc_now()
        elif to_status == TaskStatus.DONE.value:
            task.status = TaskStatus.DONE

        new_status = task.status.value if hasattr(task.status, "value") else str(task.status)
        ev = TaskEvent(
            task_id=int(task.id),
            actor_user_id=int(actor.id),
            type=TaskEventType.STATUS_CHANGED,
            payload={"from": old_status, "to": new_status, "comment": comment_str or None},
        )
        self.repo.session.add(ev)
        await self.repo.session.flush()

        try:
            recipients: list[int] = []
            assignees = list(getattr(task, "assignees", None) or [])
            executor_ids: list[int] = [int(u.id) for u in assignees]
            if not executor_ids:
                sb = getattr(task, "started_by_user_id", None)
                if sb is not None:
                    executor_ids = [int(sb)]

            if old_status == TaskStatus.NEW.value and new_status == TaskStatus.IN_PROGRESS.value:
                recipients = [int(getattr(task, "created_by_user_id"))]
            elif old_status == TaskStatus.IN_PROGRESS.value and new_status == TaskStatus.REVIEW.value:
                recipients = [int(getattr(task, "created_by_user_id"))]
            elif old_status == TaskStatus.REVIEW.value and new_status == TaskStatus.DONE.value:
                recipients = list(executor_ids)
            elif old_status == TaskStatus.REVIEW.value and new_status == TaskStatus.IN_PROGRESS.value:
                recipients = list(executor_ids)

            recipients = [r for r in recipients if r and int(r) != int(actor.id)]
            if recipients:
                ns = TaskNotificationService(self.repo.session)
                actor_name = _user_name(actor)
                for rid in recipients:
                    await ns.enqueue(
                        task_id=int(task.id),
                        recipient_user_id=int(rid),
                        type="status_changed",
                        payload={
                            "task_id": int(task.id),
                            "from": str(old_status),
                            "to": str(new_status),
                            "comment": comment_str or None,
                            "actor_user_id": int(actor.id),
                            "actor_name": actor_name,
                            "event_id": int(getattr(ev, "id", 0) or 0),
                        },
                        dedupe_key=f"status:{int(getattr(ev, 'id', 0) or 0)}",
                    )
        except Exception:
            pass
        return True, new_status

    def render_task_list_title(self, kind: str) -> str:
        return {
            "my": "✅ <b>Мои задачи</b>",
            "available": "🆕 <b>Новые (общие)</b>",
            "in_progress": "▶️ <b>В работе</b>",
            "review": "🔎 <b>На проверке</b>",
            "done": "✅ <b>Выполнено</b>",
            "archived": "🗄 <b>Архив</b>",
            "all": "📋 <b>Все задачи</b>",
        }.get(kind, "✅ <b>Задачи</b>")

    def render_task_button_title(self, task) -> str:
        pr = task.priority.value if hasattr(task.priority, "value") else str(task.priority)
        emoji = "🔥" if pr == TaskPriority.URGENT.value else "📌"
        title = esc(getattr(task, "title", "") or "")
        if len(title) > 60:
            title = title[:57] + "…"
        return f"{emoji} {title}"

    def render_task_detail_html(self, task, *, perms: TaskPermissions | None, comments_limit: int = 5) -> str:
        title = esc(getattr(task, "title", ""))
        desc = esc(getattr(task, "description", "") or "")
        st = task.status.value if hasattr(task.status, "value") else str(task.status)
        pr = task.priority.value if hasattr(task.priority, "value") else str(task.priority)
        due_at = getattr(task, "due_at", None)
        due_str = format_moscow(due_at, "%d.%m.%Y %H:%M") if due_at else ""

        assignees = list(getattr(task, "assignees", None) or [])
        created_by = getattr(task, "created_by_user", None)
        started_by = getattr(task, "started_by_user", None)

        created_at = getattr(task, "created_at", None)
        created_at_str = format_moscow(created_at, "%d.%m.%Y %H:%M") if created_at else ""
        elapsed_str = _elapsed_hm(created_at)

        lines: list[str] = []
        lines.append(f"<b>{title}</b>")
        lines.append("")
        lines.append(f"<b>ID:</b> {int(task.id)}")
        lines.append(f"<b>Статус:</b> {_status_human(st)}")
        lines.append(f"<b>Приоритет:</b> {_priority_human(pr)}")

        if created_at_str:
            lines.append(f"🕒 <b>Создано:</b> {esc(created_at_str)}")
        else:
            lines.append("🕒 <b>Создано:</b> —")

        lines.append(f"⏱ <b>Прошло:</b> {esc(elapsed_str)}")

        if created_by:
            lines.append(f"👤 <b>Поставил:</b> {esc(_user_name(created_by))}")
        else:
            lines.append("👤 <b>Поставил:</b> —")
        if due_str:
            lines.append(f"<b>Дедлайн (МСК):</b> {esc(due_str)}")

        if assignees:
            lines.append(f"<b>Исполнители:</b> {esc(', '.join(_user_name(u) for u in assignees))}")
        else:
            lines.append("<b>Исполнители:</b> Общая")

        if not assignees and started_by:
            lines.append(f"<b>Взял в работу:</b> {esc(_user_name(started_by))}")

        if created_by:
            lines.append(f"<b>Создал:</b> {esc(_user_name(created_by))}")

        if desc:
            lines.append("")
            lines.append(f"<b>Описание:</b>\n{desc}")

        # last comments
        comments = list(getattr(task, "comments", None) or [])
        if comments:
            lines.append("")
            lines.append("<b>Комментарии:</b>")
            show = comments[-comments_limit:]
            for c in show:
                author = getattr(c, "author_user", None)
                created_at = getattr(c, "created_at", None)
                created_str = format_moscow(created_at, "%d.%m.%Y %H:%M") if created_at else ""
                text = esc(getattr(c, "text", "") or "")
                photos = list(getattr(c, "photos", None) or [])
                photos_note = f" (+{len(photos)} фото)" if photos else ""
                lines.append(f"— <b>{esc(_user_name(author))}</b> [{esc(created_str)}]{photos_note}")
                if text:
                    lines.append(text)
        else:
            lines.append("")
            lines.append("<b>Комментарии:</b> —")

        if perms and perms.send_back and st == TaskStatus.REVIEW.value:
            lines.append("")
            lines.append("ℹ️ Для " + "<b>На доработку</b>" + " нужен обязательный комментарий.")

        return "\n".join(lines)
