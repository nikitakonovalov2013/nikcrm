"""shift swap requests

Revision ID: 20260115_0025
Revises: 20260115_0024
Create Date: 2026-01-15

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


revision = "20260115_0025"
down_revision = "20260115_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    status_enum = ENUM(
        "open",
        "accepted",
        "cancelled",
        "expired",
        name="shift_swap_request_status_enum",
        create_type=False,
    )

    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE shift_swap_request_status_enum AS ENUM ('open','accepted','cancelled','expired');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )

    op.create_table(
        "shift_swap_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("from_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("planned_hours", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column("bonus_amount", sa.Integer(), nullable=True),
        sa.Column("status", status_enum, nullable=False, server_default=sa.text("'open'")),
        sa.Column("accepted_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_shift_swap_requests_day", "shift_swap_requests", ["day"], unique=False)
    op.create_index("ix_shift_swap_requests_from_user_id", "shift_swap_requests", ["from_user_id"], unique=False)
    op.create_index("ix_shift_swap_requests_status", "shift_swap_requests", ["status"], unique=False)
    op.create_index("ix_shift_swap_requests_day_status", "shift_swap_requests", ["day", "status"], unique=False)

    op.alter_column("shift_swap_requests", "status", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_shift_swap_requests_day_status", table_name="shift_swap_requests")
    op.drop_index("ix_shift_swap_requests_status", table_name="shift_swap_requests")
    op.drop_index("ix_shift_swap_requests_from_user_id", table_name="shift_swap_requests")
    op.drop_index("ix_shift_swap_requests_day", table_name="shift_swap_requests")
    op.drop_table("shift_swap_requests")

    op.execute("DROP TYPE IF EXISTS shift_swap_request_status_enum CASCADE")
