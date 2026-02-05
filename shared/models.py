from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, BigInteger, Date, ForeignKey, JSON, DateTime, Boolean, Index, Table, Column, Text, Time
from sqlalchemy import Numeric
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy import UniqueConstraint
from datetime import datetime, date, time
from typing import Optional
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
    ShiftInstanceStatus,
    ShiftSwapRequestStatus,
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


class WorkShiftDay(Base):
    __tablename__ = "work_shift_days"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(20))
    hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    start_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_snooze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_followup_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped["User"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_work_shift_days_user_day"),
        Index("ix_work_shift_days_kind", "kind"),
    )


class ShiftInstance(Base):
    __tablename__ = "shift_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)

    planned_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[ShiftInstanceStatus] = mapped_column(
        PG_ENUM(
            ShiftInstanceStatus,
            name="shift_instance_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        default=ShiftInstanceStatus.PLANNED,
    )

    base_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    extra_hours: Mapped[int] = mapped_column(Integer, default=0)
    overtime_hours: Mapped[int] = mapped_column(Integer, default=0)
    extra_hour_rate: Mapped[int] = mapped_column(Integer, default=300)
    overtime_hour_rate: Mapped[int] = mapped_column(Integer, default=400)

    amount_default: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_submitted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_approved: Mapped[int | None] = mapped_column(Integer, nullable=True)

    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped["User"] = relationship(lazy="selectin", foreign_keys=[user_id])
    approved_by_user: Mapped[Optional["User"]] = relationship(lazy="selectin", foreign_keys=[approved_by_user_id])

    events: Mapped[list["ShiftInstanceEvent"]] = relationship(
        back_populates="shift",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_shift_instances_user_day"),
        Index("ix_shift_instances_status", "status"),
        Index("ix_shift_instances_day_status", "day", "status"),
    )


class ShiftInstanceEvent(Base):
    __tablename__ = "shift_instance_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shift_instances.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    shift: Mapped["ShiftInstance"] = relationship(back_populates="events")
    actor_user: Mapped[Optional["User"]] = relationship(lazy="selectin")

    __table_args__ = (
        Index("ix_shift_instance_events_shift_created_at", "shift_id", "created_at"),
    )


class ShiftSwapRequest(Base):
    __tablename__ = "shift_swap_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    planned_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(50))
    bonus_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[ShiftSwapRequestStatus] = mapped_column(
        PG_ENUM(
            ShiftSwapRequestStatus,
            name="shift_swap_request_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        default=ShiftSwapRequestStatus.OPEN,
    )

    accepted_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    from_user: Mapped["User"] = relationship(lazy="selectin", foreign_keys=[from_user_id])
    accepted_by_user: Mapped[Optional["User"]] = relationship(lazy="selectin", foreign_keys=[accepted_by_user_id])

    __table_args__ = (
        Index("ix_shift_swap_requests_status", "status"),
        Index("ix_shift_swap_requests_day_status", "day", "status"),
    )


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


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sent_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    target_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="all")
    filter_positions: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    filter_user_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)

    cta_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cta_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")

    total_recipients: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    no_tg_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tg_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    sent_by_user: Mapped[Optional["User"]] = relationship(lazy="selectin", foreign_keys=[sent_by_user_id])
    deliveries: Mapped[list["BroadcastDelivery"]] = relationship(
        back_populates="broadcast",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    ratings: Mapped[list["BroadcastRating"]] = relationship(
        back_populates="broadcast",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_broadcasts_created_at", "created_at"),
        Index("ix_broadcasts_sent_at", "sent_at"),
        Index("ix_broadcasts_sent_by_user_id", "sent_by_user_id"),
    )


class BroadcastDelivery(Base):
    __tablename__ = "broadcast_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(32), nullable=False, default="no_tg")
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    broadcast: Mapped["Broadcast"] = relationship(back_populates="deliveries")
    user: Mapped[Optional["User"]] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_deliveries_broadcast_user"),
        Index("ix_broadcast_deliveries_status", "delivery_status"),
        Index("ix_broadcast_deliveries_broadcast_status", "broadcast_id", "delivery_status"),
    )


class BroadcastRating(Base):
    __tablename__ = "broadcast_ratings"

    id: Mapped[int] = mapped_column(primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(ForeignKey("broadcasts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    rated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    broadcast: Mapped["Broadcast"] = relationship(back_populates="ratings")
    user: Mapped["User"] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_ratings_broadcast_user"),
        Index("ix_broadcast_ratings_broadcast_id", "broadcast_id"),
        Index("ix_broadcast_ratings_user_id", "user_id"),
    )


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
    tg_photo_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tg_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tg_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    photo_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    taken_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bought_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    bought_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    archived_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PurchaseStatus] = mapped_column(
        PG_ENUM(
            PurchaseStatus,
            name="purchase_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        default=PurchaseStatus.NEW,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    taken_by_user: Mapped[User | None] = relationship(foreign_keys=[taken_by_user_id])
    bought_by_user: Mapped[User | None] = relationship(foreign_keys=[bought_by_user_id])
    approved_by_user: Mapped[User | None] = relationship(foreign_keys=[approved_by_user_id])
    archived_by_user: Mapped[User | None] = relationship(foreign_keys=[archived_by_user_id])
    events: Mapped[list["PurchaseEvent"]] = relationship(
        back_populates="purchase",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_purchases_status", "status"),
        Index("ix_purchases_taken_by_user_id", "taken_by_user_id"),
        Index("ix_purchases_taken_at", "taken_at"),
        Index("ix_purchases_bought_at", "bought_at"),
        Index("ix_purchases_archived_at", "archived_at"),
    )


class PurchaseEvent(Base):
    __tablename__ = "purchase_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_id: Mapped[int] = mapped_column(ForeignKey("purchases.id", ondelete="CASCADE"))
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(64))
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    purchase: Mapped[Purchase] = relationship(back_populates="events")
    actor_user: Mapped[User | None] = relationship(foreign_keys=[actor_user_id])

    __table_args__ = (
        Index("ix_purchase_events_purchase_id", "purchase_id"),
        Index("ix_purchase_events_created_at", "created_at"),
    )


class TelegramOutbox(Base):
    __tablename__ = "telegram_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("ix_telegram_outbox_kind", "kind"),
        Index("ix_telegram_outbox_status", "status"),
        Index("ix_telegram_outbox_next_retry_at", "next_retry_at"),
        Index("ix_telegram_outbox_status_next_retry_at", "status", "next_retry_at"),
    )


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
