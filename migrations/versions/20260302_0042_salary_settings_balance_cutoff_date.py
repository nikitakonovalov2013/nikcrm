"""salary settings: add balance cutoff date

Revision ID: 20260302_0042
Revises: 20260302_0041
Create Date: 2026-03-02

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260302_0042"
down_revision = "20260302_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "salary_settings",
        sa.Column(
            "balance_cutoff_date",
            sa.Date(),
            nullable=False,
            server_default=sa.text("'2026-03-01'"),
        ),
    )

    op.execute("UPDATE salary_settings SET balance_cutoff_date = '2026-03-01' WHERE balance_cutoff_date IS NULL")


def downgrade() -> None:
    op.drop_column("salary_settings", "balance_cutoff_date")
