from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, BigInteger, Date, ForeignKey, JSON, DateTime
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from datetime import datetime, date
from .db import Base
from .enums import UserStatus, Schedule, Position, AdminActionType


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    actions: Mapped[list["AdminAction"]] = relationship(back_populates="user", cascade="all, delete-orphan")


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="actions")
