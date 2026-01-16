"""work_shift_days: add schedule storage

Revision ID: 20260115_0022
Revises: 20260113_0021
Create Date: 2026-01-15

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260115_0022"
down_revision = "20260113_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_shift_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("hours", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "day", name="uq_work_shift_days_user_day"),
    )
    op.create_index("ix_work_shift_days_user_id", "work_shift_days", ["user_id"], unique=False)
    op.create_index("ix_work_shift_days_day", "work_shift_days", ["day"], unique=False)
    op.create_index("ix_work_shift_days_kind", "work_shift_days", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_work_shift_days_kind", table_name="work_shift_days")
    op.drop_index("ix_work_shift_days_day", table_name="work_shift_days")
    op.drop_index("ix_work_shift_days_user_id", table_name="work_shift_days")
    op.drop_table("work_shift_days")
