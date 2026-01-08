from __future__ import annotations

from sqlalchemy import select, exists, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enums import TaskStatus, TaskPriority, TaskEventType, UserStatus
from shared.models import Task, TaskComment, TaskCommentPhoto, TaskEvent, User, task_assignees


class TaskRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_user_by_tg_id(self, tg_id: int) -> User | None:
        res = await self.session.execute(select(User).where(User.tg_id == tg_id).where(User.is_deleted == False))
        return res.scalar_one_or_none()

    async def get_user_by_tg_id_any(self, tg_id: int) -> User | None:
        res = await self.session.execute(select(User).where(User.tg_id == tg_id))
        return res.scalar_one_or_none()

    async def get_task_full(self, task_id: int) -> Task | None:
        q = (
            select(Task)
            .where(Task.id == task_id)
            .options(
                selectinload(Task.assignees),
                selectinload(Task.created_by_user),
                selectinload(Task.started_by_user),
                selectinload(Task.completed_by_user),
                selectinload(Task.comments).selectinload(TaskComment.author_user),
                selectinload(Task.comments).selectinload(TaskComment.photos),
            )
        )
        res = await self.session.execute(q)
        return res.scalar_one_or_none()

    def _q_base(self):
        return (
            select(Task)
            .options(
                selectinload(Task.assignees),
                selectinload(Task.created_by_user),
                selectinload(Task.started_by_user),
                selectinload(Task.completed_by_user),
            )
        )

    async def list_tasks(
        self,
        *,
        kind: str,
        actor_user_id: int,
        is_admin_or_manager: bool,
        page: int,
        limit: int,
    ) -> tuple[list[Task], bool, bool]:
        page = max(0, int(page))
        offset = page * limit

        q = self._q_base().order_by(Task.created_at.desc(), Task.id.desc())

        has_any_acl = exists(select(1).where(task_assignees.c.task_id == Task.id))
        has_me = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(actor_user_id)))
        )

        if kind == "available":
            q = q.where(Task.status == TaskStatus.NEW).where(~has_any_acl)
        elif kind == "my":
            q = q.where(has_me).where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE]))
        elif kind in {"in_progress", "review", "done", "archived"}:
            status = {
                "in_progress": TaskStatus.IN_PROGRESS,
                "review": TaskStatus.REVIEW,
                "done": TaskStatus.DONE,
                "archived": TaskStatus.ARCHIVED,
            }[kind]
            q = q.where(Task.status == status)
            if not is_admin_or_manager:
                # For regular staff: only tasks assigned to me OR common started by me
                q = q.where(or_(has_me, and_(~has_any_acl, Task.started_by_user_id == int(actor_user_id))))
        elif kind == "all":
            q = q.where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE, TaskStatus.ARCHIVED]))
            if not is_admin_or_manager:
                q = q.where(or_(has_me, and_(~has_any_acl, Task.started_by_user_id == int(actor_user_id))))
        else:
            # default: all visible
            q = q.where(Task.status.in_([TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.REVIEW, TaskStatus.DONE]))
            if not is_admin_or_manager:
                q = q.where(or_(has_me, and_(~has_any_acl, Task.started_by_user_id == int(actor_user_id))))

        # Fetch limit+1 for pagination
        res = await self.session.execute(q.offset(offset).limit(limit + 1))
        items = list(res.scalars().unique().all())

        has_prev = page > 0
        has_next = len(items) > limit
        if has_next:
            items = items[:limit]

        return items, has_prev, has_next


    async def list_tasks_by_scope_status(
        self,
        *,
        scope: str,
        status: str,
        actor_user_id: int,
        is_admin_or_manager: bool,
        page: int,
        limit: int,
    ) -> tuple[list[Task], bool, bool]:
        page = max(0, int(page))
        offset = page * limit

        q = self._q_base().order_by(Task.created_at.desc(), Task.id.desc())

        has_any_acl = exists(select(1).where(task_assignees.c.task_id == Task.id))
        has_me = exists(
            select(1).where(and_(task_assignees.c.task_id == Task.id, task_assignees.c.user_id == int(actor_user_id)))
        )

        st = {
            TaskStatus.NEW.value: TaskStatus.NEW,
            TaskStatus.IN_PROGRESS.value: TaskStatus.IN_PROGRESS,
            TaskStatus.REVIEW.value: TaskStatus.REVIEW,
            TaskStatus.DONE.value: TaskStatus.DONE,
            TaskStatus.ARCHIVED.value: TaskStatus.ARCHIVED,
        }.get(str(status), None)
        if st is None:
            st = TaskStatus.NEW

        q = q.where(Task.status == st)

        if scope == "all":
            if not is_admin_or_manager:
                q = q.where(or_(has_me, and_(~has_any_acl, Task.started_by_user_id == int(actor_user_id))))
        else:
            if st == TaskStatus.NEW:
                q = q.where(has_me)
            else:
                q = q.where(or_(has_me, and_(~has_any_acl, Task.started_by_user_id == int(actor_user_id))))

        res = await self.session.execute(q.offset(offset).limit(limit + 1))
        items = list(res.scalars().unique().all())

        has_prev = page > 0
        has_next = len(items) > limit
        if has_next:
            items = items[:limit]

        return items, has_prev, has_next

    async def add_comment(self, *, task_id: int, author_user_id: int, text: str | None, photo_file_ids: list[str]) -> TaskComment:
        c = TaskComment(task_id=int(task_id), author_user_id=int(author_user_id), text=(text or None))
        self.session.add(c)
        await self.session.flush()

        for fid in photo_file_ids:
            self.session.add(TaskCommentPhoto(comment_id=int(c.id), tg_file_id=str(fid)))
        await self.session.flush()
        await self.session.refresh(c)
        return c


    async def update_task_photo_storage(
        self,
        *,
        task_id: int,
        photo_key: str | None,
        photo_url: str | None,
        photo_path: str | None,
        tg_photo_file_id: str | None,
    ) -> None:
        res = await self.session.execute(select(Task).where(Task.id == int(task_id)))
        t = res.scalar_one_or_none()
        if not t:
            return

        if photo_key:
            try:
                if hasattr(t, "photo_key"):
                    t.photo_key = str(photo_key)
            except Exception:
                pass
        if photo_path:
            try:
                if hasattr(t, "photo_path"):
                    t.photo_path = str(photo_path)
            except Exception:
                pass
        if photo_url:
            try:
                if hasattr(t, "photo_url"):
                    t.photo_url = str(photo_url)
            except Exception:
                pass

        if tg_photo_file_id:
            try:
                if hasattr(t, "tg_photo_file_id"):
                    t.tg_photo_file_id = str(tg_photo_file_id)
                elif hasattr(t, "photo_file_id"):
                    t.photo_file_id = str(tg_photo_file_id)
            except Exception:
                pass

        await self.session.flush()


    async def list_assignable_users(self) -> list[User]:
        res = await self.session.execute(
            select(User)
            .where(User.is_deleted == False)
            .where(User.status == UserStatus.APPROVED)
            .order_by(User.first_name, User.last_name, User.id)
        )
        return list(res.scalars().all())


    async def create_task(
        self,
        *,
        title: str,
        description: str | None,
        priority: str,
        due_at,
        created_by_user_id: int,
        assignee_user_ids: list[int],
        photo_file_id: str | None,
    ) -> Task:
        pr = TaskPriority.URGENT if str(priority) == TaskPriority.URGENT.value else TaskPriority.NORMAL

        users: list[User] = []
        if assignee_user_ids:
            res = await self.session.execute(
                select(User)
                .where(User.id.in_([int(x) for x in assignee_user_ids]))
                .where(User.is_deleted == False)
                .where(User.status == UserStatus.APPROVED)
            )
            users = list(res.scalars().all())

        t = Task(
            title=str(title).strip(),
            description=(str(description).strip() if description and str(description).strip() else None),
            priority=pr,
            due_at=due_at,
            status=TaskStatus.NEW,
            created_by_user_id=int(created_by_user_id),
            assignees=users,
            photo_file_id=(str(photo_file_id) if photo_file_id else None),
        )
        # Assign cache field explicitly to avoid crashing if runtime model doesn't have the column.
        try:
            if photo_file_id and hasattr(t, "tg_photo_file_id"):
                t.tg_photo_file_id = str(photo_file_id)
        except Exception:
            pass
        self.session.add(t)
        await self.session.flush()

        self.session.add(
            TaskEvent(
                task_id=int(t.id),
                actor_user_id=int(created_by_user_id),
                type=TaskEventType.CREATED,
                payload=None,
            )
        )
        await self.session.flush()
        await self.session.refresh(t)
        return t


    async def update_task_photo(self, *, task_id: int, photo_url: str | None, tg_photo_file_id: str | None) -> None:
        res = await self.session.execute(select(Task).where(Task.id == int(task_id)))
        t = res.scalar_one_or_none()
        if not t:
            return
        if photo_url:
            try:
                if hasattr(t, "photo_url"):
                    t.photo_url = str(photo_url)
            except Exception:
                pass
        if tg_photo_file_id:
            try:
                if hasattr(t, "tg_photo_file_id"):
                    t.tg_photo_file_id = str(tg_photo_file_id)
                elif hasattr(t, "photo_file_id"):
                    # fallback for older schema
                    t.photo_file_id = str(tg_photo_file_id)
            except Exception:
                pass
        await self.session.flush()
