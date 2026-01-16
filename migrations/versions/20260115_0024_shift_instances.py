"""shift_instances: add shift accounting entities

Revision ID: 20260115_0024
Revises: 20260115_0023
Create Date: 2026-01-15

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


# revision identifiers, used by Alembic.
revision = "20260115_0024"
down_revision = "20260115_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    shift_status_enum = ENUM(
        "planned",
        "started",
        "closed",
        "pending_approval",
        "approved",
        "rejected",
        "needs_rework",
        name="shift_instance_status_enum",
        create_type=False,
    )

    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE shift_instance_status_enum AS ENUM (
                'planned',
                'started',
                'closed',
                'pending_approval',
                'approved',
                'rejected',
                'needs_rework'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )

    op.create_table(
        "shift_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("planned_hours", sa.Integer(), nullable=True),
        sa.Column("is_emergency", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", shift_status_enum, nullable=False, server_default=sa.text("'planned'")),
        sa.Column("base_rate", sa.Integer(), nullable=True),
        sa.Column("extra_hours", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("overtime_hours", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("extra_hour_rate", sa.Integer(), nullable=False, server_default=sa.text("300")),
        sa.Column("overtime_hour_rate", sa.Integer(), nullable=False, server_default=sa.text("400")),
        sa.Column("amount_default", sa.Integer(), nullable=True),
        sa.Column("amount_submitted", sa.Integer(), nullable=True),
        sa.Column("amount_approved", sa.Integer(), nullable=True),
        sa.Column("approval_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("user_id", "day", name="uq_shift_instances_user_day"),
    )

    op.create_index("ix_shift_instances_user_id", "shift_instances", ["user_id"], unique=False)
    op.create_index("ix_shift_instances_day", "shift_instances", ["day"], unique=False)
    op.create_index("ix_shift_instances_status", "shift_instances", ["status"], unique=False)
    op.create_index("ix_shift_instances_day_status", "shift_instances", ["day", "status"], unique=False)

    op.create_table(
        "shift_instance_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("shift_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_index("ix_shift_instance_events_shift_id", "shift_instance_events", ["shift_id"], unique=False)
    op.create_index(
        "ix_shift_instance_events_shift_created_at",
        "shift_instance_events",
        ["shift_id", "created_at"],
        unique=False,
    )

    op.alter_column("shift_instances", "status", server_default=None)
    op.alter_column("shift_instances", "is_emergency", server_default=None)
    op.alter_column("shift_instances", "extra_hours", server_default=None)
    op.alter_column("shift_instances", "overtime_hours", server_default=None)
    op.alter_column("shift_instances", "extra_hour_rate", server_default=None)
    op.alter_column("shift_instances", "overtime_hour_rate", server_default=None)
    op.alter_column("shift_instances", "approval_required", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_shift_instance_events_shift_created_at", table_name="shift_instance_events")
    op.drop_index("ix_shift_instance_events_shift_id", table_name="shift_instance_events")
    op.drop_table("shift_instance_events")

    op.drop_index("ix_shift_instances_day_status", table_name="shift_instances")
    op.drop_index("ix_shift_instances_status", table_name="shift_instances")
    op.drop_index("ix_shift_instances_day", table_name="shift_instances")
    op.drop_index("ix_shift_instances_user_id", table_name="shift_instances")
    op.drop_table("shift_instances")

    op.execute("DROP TYPE IF EXISTS shift_instance_status_enum CASCADE")
