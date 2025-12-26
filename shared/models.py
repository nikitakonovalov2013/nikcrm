from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import String, Integer, BigInteger, Date, ForeignKey, JSON, DateTime, Boolean, Index
from sqlalchemy import Numeric
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from datetime import datetime, date
from .db import Base
from .enums import UserStatus, Schedule, Position, AdminActionType, PurchaseStatus


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


class MaterialType(Base):
    __tablename__ = "material_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Material(Base):
    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_type_id: Mapped[int] = mapped_column(ForeignKey("material_types.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    short_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str] = mapped_column(String(10), default="кг")
    current_stock: Mapped[Numeric] = mapped_column(Numeric(16, 3), default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    material_type: Mapped[MaterialType] = relationship()


class MaterialConsumption(Base):
    __tablename__ = "material_consumptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    amount: Mapped[Numeric] = mapped_column(Numeric(16, 3))
    date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

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
    status: Mapped[PurchaseStatus] = mapped_column(
        PG_ENUM(
            PurchaseStatus,
            name="purchase_status_enum",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        default=PurchaseStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    user: Mapped[User] = relationship()
