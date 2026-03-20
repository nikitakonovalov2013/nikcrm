"""finance_settings: add cash_balance field

Revision ID: 20260320_0052
Revises: 20260319_0051
Create Date: 2026-03-20

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260320_0052"
down_revision = "20260319_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "finance_settings",
        sa.Column(
            "cash_balance",
            sa.Numeric(14, 2),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("finance_settings", "cash_balance")
