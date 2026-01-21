"""work_shift_days: add start_time/end_time

Revision ID: 20260119_0026
Revises: 20260115_0025
Create Date: 2026-01-19

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260119_0026"
down_revision = "20260115_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Add columns if they don't exist (safe for partially upgraded DBs)
    op.execute(
        "ALTER TABLE work_shift_days ADD COLUMN IF NOT EXISTS start_time TIME NULL"
    )
    op.execute(
        "ALTER TABLE work_shift_days ADD COLUMN IF NOT EXISTS end_time TIME NULL"
    )

    # 2) Backfill defaults for existing rows
    op.execute(
        "UPDATE work_shift_days SET start_time = TIME '10:00' WHERE start_time IS NULL"
    )
    op.execute(
        "UPDATE work_shift_days SET end_time = TIME '18:00' WHERE end_time IS NULL"
    )


def downgrade() -> None:
    # keep downgrade safe even if columns are already removed
    op.execute(
        "ALTER TABLE work_shift_days DROP COLUMN IF EXISTS start_time"
    )
    op.execute(
        "ALTER TABLE work_shift_days DROP COLUMN IF EXISTS end_time"
    )
