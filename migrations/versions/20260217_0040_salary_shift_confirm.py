"""salary shift confirmation fields

Revision ID: 20260217_0040
Revises: 20260216_0039
Create Date: 2026-02-17

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260217_0040"
down_revision = "20260216_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "salary_shift_state",
        sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.add_column(
        "salary_shift_state",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_salary_shift_state_confirmed_at", "salary_shift_state", ["confirmed_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_salary_shift_state_confirmed_at", table_name="salary_shift_state")
    op.drop_column("salary_shift_state", "confirmed_at")
    op.drop_column("salary_shift_state", "confirmed_by_user_id")
