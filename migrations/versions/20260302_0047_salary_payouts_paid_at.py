"""salary_payouts paid_at

Revision ID: 20260302_0047
Revises: 20260302_0046
Create Date: 2026-03-02

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260302_0047"
down_revision = "20260302_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Add nullable column with server default.
    op.add_column(
        "salary_payouts",
        sa.Column(
            "paid_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )

    # 2) Backfill for existing rows.
    op.execute("UPDATE salary_payouts SET paid_at = created_at WHERE paid_at IS NULL")

    # 3) Make it NOT NULL.
    op.alter_column("salary_payouts", "paid_at", nullable=False)

    # Optional: index for month filtering/sorting.
    op.create_index("ix_salary_payouts_paid_at", "salary_payouts", ["paid_at"])


def downgrade() -> None:
    op.drop_index("ix_salary_payouts_paid_at", table_name="salary_payouts")
    op.drop_column("salary_payouts", "paid_at")
