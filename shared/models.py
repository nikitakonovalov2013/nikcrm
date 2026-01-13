from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, BigInteger, Date, ForeignKey, JSON, DateTime, Boolean, Index, Table, Column, Text
from sqlalchemy import Numeric
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy import UniqueConstraint
from datetime import datetime, date, time
from .db import Base
from .enums import (
    UserStatus,
    Schedule,
    Position,
    AdminActionType,
    PurchaseStatus,
    TaskStatus,
    TaskPriority,
    TaskEventType,
)
from .utils import utc_now


material_master_access = Table(
    "material_master_access",
    Base.metadata,
    Column("material_id", ForeignKey("materials.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_material_master_access_material_id", "material_id"),
    Index("ix_material_master_access_user_id", "user_id"),
)


task_assignees = Table(
    "task_assignees",
    Base.metadata,
    Column("task_id", ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Index("ix_task_assignees_user_id", "user_id"),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Telegram user ID stored as BIGINT for full range safety
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    rate_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Postgres enum type names are explicitly set to avoid name conflicts with column names
    schedule: Mapped[Schedule | None] = mapped_column(
        PG_ENUM(
            Schedule,
            name="work_schedule_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        nullable=True,
    )
    position: Mapped[Position | None] = mapped_column(
        PG_ENUM(
            Position,
            name="user_position_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        nullable=True,
    )
    status: Mapped[UserStatus] = mapped_column(
        PG_ENUM(
            UserStatus,
            name="user_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        default=UserStatus.PENDING,
    )
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    actions: Mapped[list["AdminAction"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    tasks_created: Mapped[list["Task"]] = relationship(
        foreign_keys="Task.created_by_user_id",
        back_populates="created_by_user",
    )
    tasks_started: Mapped[list["Task"]] = relationship(
        foreign_keys="Task.started_by_user_id",
        back_populates="started_by_user",
    )
    tasks_completed: Mapped[list["Task"]] = relationship(
        foreign_keys="Task.completed_by_user_id",
        back_populates="completed_by_user",
    )
    assigned_tasks: Mapped[list["Task"]] = relationship(
        secondary=task_assignees,
        back_populates="assignees",
        lazy="selectin",
    )
    task_comments: Mapped[list["TaskComment"]] = relationship(
        back_populates="author_user",
        cascade="all, delete-orphan",
    )
    task_events: Mapped[list["TaskEvent"]] = relationship(
        back_populates="actor_user",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_users_is_deleted", "is_deleted"),
    )


class MagicLinkToken(Base):
    __tablename__ = "magic_link_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped["User"] = relationship()


class AdminAction(Base):
    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Telegram admin ID stored as BIGINT for full range safety
    admin_tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[AdminActionType] = mapped_column(
        PG_ENUM(
            AdminActionType,
            name="admin_action_type_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        )
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="actions")


class MaterialType(Base):
    __tablename__ = "material_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_type_id: Mapped[int] = mapped_column(ForeignKey("material_types.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    short_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str] = mapped_column(String(10), default="кг")
    current_stock: Mapped[Numeric] = mapped_column(Numeric(16, 3), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    material_type: Mapped[MaterialType] = relationship()
    allowed_masters: Mapped[list["User"]] = relationship(
        secondary=material_master_access,
        backref="accessible_materials",
    )


class MaterialConsumption(Base):
    __tablename__ = "material_consumptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    amount: Mapped[Numeric] = mapped_column(Numeric(16, 3))
    date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    material: Mapped[Material] = relationship()
    employee: Mapped[User] = relationship()

    __table_args__ = (
        Index("ix_material_consumptions_material_id", "material_id"),
        Index("ix_material_consumptions_date", "date"),
    )


class MaterialSupply(Base):
    __tablename__ = "material_supplies"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"))
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    amount: Mapped[Numeric] = mapped_column(Numeric(16, 3))
    date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    material: Mapped[Material] = relationship()
    employee: Mapped[User | None] = relationship()

    __table_args__ = (
        Index("ix_material_supplies_material_id", "material_id"),
        Index("ix_material_supplies_date", "date"),
    )


class Purchase(Base):
    __tablename__ = "purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(String(2000))
    photo_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[PurchaseStatus] = mapped_column(
        PG_ENUM(
            PurchaseStatus,
            name="purchase_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        default=PurchaseStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship()


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    photo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    tg_photo_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        PG_ENUM(
            TaskStatus,
            name="task_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        default=TaskStatus.NEW,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        PG_ENUM(
            TaskPriority,
            name="task_priority_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        default=TaskPriority.NORMAL,
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    started_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by_user: Mapped[User] = relationship(
        foreign_keys=[created_by_user_id],
        back_populates="tasks_created",
    )
    started_by_user: Mapped[User | None] = relationship(
        foreign_keys=[started_by_user_id],
        back_populates="tasks_started",
    )
    completed_by_user: Mapped[User | None] = relationship(
        foreign_keys=[completed_by_user_id],
        back_populates="tasks_completed",
    )
    assignees: Mapped[list[User]] = relationship(
        secondary=task_assignees,
        back_populates="assigned_tasks",
        lazy="selectin",
    )
    comments: Mapped[list["TaskComment"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    events: Mapped[list["TaskEvent"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_priority", "priority"),
        Index("ix_tasks_due_at", "due_at"),
        Index("ix_tasks_created_at", "created_at"),
        Index("ix_tasks_started_by_user_id", "started_by_user_id"),
        Index("ix_tasks_archived_at", "archived_at"),
    )


class TaskComment(Base):
    __tablename__ = "task_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[Task] = relationship(back_populates="comments")
    author_user: Mapped[User] = relationship(back_populates="task_comments")
    photos: Mapped[list["TaskCommentPhoto"]] = relationship(
        back_populates="comment",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_task_comments_task_id", "task_id"),
        Index("ix_task_comments_created_at", "created_at"),
    )


class TaskCommentPhoto(Base):
    __tablename__ = "task_comment_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("task_comments.id", ondelete="CASCADE"))
    tg_file_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    comment: Mapped[TaskComment] = relationship(back_populates="photos")

    __table_args__ = (
        Index("ix_task_comment_photos_comment_id", "comment_id"),
    )


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    type: Mapped[TaskEventType] = mapped_column(
        PG_ENUM(
            TaskEventType,
            name="task_event_type_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        )
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    task: Mapped[Task] = relationship(back_populates="events")
    actor_user: Mapped[User] = relationship(back_populates="task_events")

    __table_args__ = (
        Index("ix_task_events_task_id", "task_id"),
        Index("ix_task_events_created_at", "created_at"),
    )


class TaskNotification(Base):
    __tablename__ = "task_notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    recipient_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    task: Mapped[Task] = relationship(lazy="selectin")
    recipient_user: Mapped[User] = relationship(lazy="selectin")

    __table_args__ = (
        Index("ix_task_notifications_status_scheduled_at", "status", "scheduled_at"),
        Index("ix_task_notifications_recipient_status", "recipient_user_id", "status"),
        UniqueConstraint("recipient_user_id", "dedupe_key", name="uq_task_notifications_recipient_dedupe"),
    )


class ReminderSettings(Base):
    __tablename__ = "reminder_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    reminder_time: Mapped[time] = mapped_column(default=time(16, 0))
    skip_weekends: Mapped[bool] = mapped_column(Boolean, default=True)
    send_to_admins: Mapped[bool] = mapped_column(Boolean, default=True)
    send_to_managers: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_report_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_report_time: Mapped[time] = mapped_column(default=time(18, 0))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
