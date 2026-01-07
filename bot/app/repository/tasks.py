from __future__ import annotations

from sqlalchemy import select, exists, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.enums import TaskStatus
from shared.models import Task, TaskComment, TaskCommentPhoto, User, task_assignees


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

    async def add_comment(self, *, task_id: int, author_user_id: int, text: str | None, photo_file_ids: list[str]) -> TaskComment:
        c = TaskComment(task_id=int(task_id), author_user_id=int(author_user_id), text=(text or None))
        self.session.add(c)
        await self.session.flush()

        for fid in photo_file_ids:
            self.session.add(TaskCommentPhoto(comment_id=int(c.id), tg_file_id=str(fid)))
        await self.session.flush()
        await self.session.refresh(c)
        return c
