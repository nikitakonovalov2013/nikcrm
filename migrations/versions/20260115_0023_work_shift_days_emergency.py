"""work_shift_days: add emergency flag and comment

Revision ID: 20260115_0023
Revises: 20260115_0022
Create Date: 2026-01-15

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260115_0023"
down_revision = "20260115_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("work_shift_days", sa.Column("is_emergency", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("work_shift_days", sa.Column("comment", sa.String(length=1000), nullable=True))

    # Drop server default after backfill (keep python default in ORM)
    op.alter_column("work_shift_days", "is_emergency", server_default=None)


def downgrade() -> None:
    op.drop_column("work_shift_days", "comment")
    op.drop_column("work_shift_days", "is_emergency")
