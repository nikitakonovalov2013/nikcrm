"""salary payouts: add (user_id, period_start, period_end) index

Revision ID: 20260302_0041
Revises: 20260217_0040
Create Date: 2026-03-02

"""

from __future__ import annotations

from alembic import op


revision = "20260302_0041"
down_revision = "20260217_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_salary_payouts_user_period",
        "salary_payouts",
        ["user_id", "period_start", "period_end"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_salary_payouts_user_period", table_name="salary_payouts")
