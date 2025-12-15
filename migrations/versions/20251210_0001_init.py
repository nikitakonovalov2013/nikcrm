"""init tables

Revision ID: 20251210_0001
Revises: 
Create Date: 2025-12-10 13:20:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM
from shared.enums import UserStatus, Schedule, Position, AdminActionType

# revision identifiers, used by Alembic.
revision = "20251210_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Define enums on columns with create_type=False to avoid duplicate CREATE TYPE during table create.
    schedule_enum = ENUM(
        Schedule,
        name="work_schedule_enum",
        create_type=False,
        values_callable=lambda obj: [e.value for e in obj],
    )
    position_enum = ENUM(
        Position,
        name="user_position_enum",
        create_type=False,
        values_callable=lambda obj: [e.value for e in obj],
    )
    status_enum = ENUM(
        UserStatus,
        name="user_status_enum",
        create_type=False,
        values_callable=lambda obj: [e.value for e in obj],
    )
    action_enum = ENUM(
        AdminActionType,
        name="admin_action_type_enum",
        create_type=False,
        values_callable=lambda obj: [e.value for e in obj],
    )

    # Create enum types idempotently (ignore if already exists)
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE work_schedule_enum AS ENUM ('2/2','5/2','4/3');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )
    # If the type exists with old labels, rename them to the new StrEnum values
    op.execute(
        """
        DO $$
        DECLARE t oid;
        BEGIN
            SELECT oid INTO t FROM pg_type WHERE typname = 'work_schedule_enum';
            IF t IS NOT NULL THEN
                IF EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'TWO_TWO' AND enumtypid = t) THEN
                    EXECUTE 'ALTER TYPE work_schedule_enum RENAME VALUE ''TWO_TWO'' TO ''2/2''';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'FIVE_TWO' AND enumtypid = t) THEN
                    EXECUTE 'ALTER TYPE work_schedule_enum RENAME VALUE ''FIVE_TWO'' TO ''5/2''';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'FOUR_THREE' AND enumtypid = t) THEN
                    EXECUTE 'ALTER TYPE work_schedule_enum RENAME VALUE ''FOUR_THREE'' TO ''4/3''';
                END IF;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE user_position_enum AS ENUM ('Руководитель','Сборщик заказов','Упаковщик','Мастер');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )
    # If the type exists with English labels, rename them to Russian labels used by StrEnum
    op.execute(
        """
        DO $$
        DECLARE t oid;
        BEGIN
            SELECT oid INTO t FROM pg_type WHERE typname = 'user_position_enum';
            IF t IS NOT NULL THEN
                IF EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'MANAGER' AND enumtypid = t) THEN
                    EXECUTE 'ALTER TYPE user_position_enum RENAME VALUE ''MANAGER'' TO ''Руководитель''';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'PICKER' AND enumtypid = t) THEN
                    EXECUTE 'ALTER TYPE user_position_enum RENAME VALUE ''PICKER'' TO ''Сборщик заказов''';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'PACKER' AND enumtypid = t) THEN
                    EXECUTE 'ALTER TYPE user_position_enum RENAME VALUE ''PACKER'' TO ''Упаковщик''';
                END IF;
                IF EXISTS (SELECT 1 FROM pg_enum WHERE enumlabel = 'MASTER' AND enumtypid = t) THEN
                    EXECUTE 'ALTER TYPE user_position_enum RENAME VALUE ''MASTER'' TO ''Мастер''';
                END IF;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE user_status_enum AS ENUM ('PENDING','APPROVED','REJECTED','BLACKLISTED');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE admin_action_type_enum AS ENUM ('APPROVE','REJECT','BLACKLIST','EDIT','MESSAGE','BROADCAST');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tg_id", sa.BigInteger(), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("rate_k", sa.Integer(), nullable=True),
        sa.Column("schedule", schedule_enum, nullable=True),
        sa.Column("position", position_enum, nullable=True),
        sa.Column("status", status_enum, nullable=False, server_default=UserStatus.PENDING.value),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("tg_id", name="uq_users_tg_id"),
    )
    op.create_index("ix_tg_id", "users", ["tg_id"]) 

    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_tg_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", action_enum, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_admin_tg_id", "admin_actions", ["admin_tg_id"]) 


def downgrade() -> None:
    op.drop_index("ix_admin_tg_id", table_name="admin_actions")
    op.drop_table("admin_actions")

    op.drop_index("ix_tg_id", table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS work_schedule_enum CASCADE")
    op.execute("DROP TYPE IF EXISTS user_position_enum CASCADE")
    op.execute("DROP TYPE IF EXISTS user_status_enum CASCADE")
    op.execute("DROP TYPE IF EXISTS admin_action_type_enum CASCADE")
