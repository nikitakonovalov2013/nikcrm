"""salaries: link payouts to shifts

Revision ID: 20260302_0046
Revises: 20260302_0045
Create Date: 2026-03-02

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260302_0046"
down_revision = "20260302_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "salary_payout_shifts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payout_id", sa.Integer(), sa.ForeignKey("salary_payouts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("shift_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("shift_id", name="uq_salary_payout_shifts_shift_id"),
    )
    op.create_index("ix_salary_payout_shifts_payout_id", "salary_payout_shifts", ["payout_id"], unique=False)
    op.create_index("ix_salary_payout_shifts_shift_id", "salary_payout_shifts", ["shift_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_salary_payout_shifts_shift_id", table_name="salary_payout_shifts")
    op.drop_index("ix_salary_payout_shifts_payout_id", table_name="salary_payout_shifts")
    op.drop_table("salary_payout_shifts")
