"""work_shift_days: notification flags

Revision ID: 20260119_0027
Revises: 20260119_0026
Create Date: 2026-01-19

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260119_0027"
down_revision = "20260119_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE work_shift_days ADD COLUMN IF NOT EXISTS start_notified_at TIMESTAMPTZ NULL")
    op.execute("ALTER TABLE work_shift_days ADD COLUMN IF NOT EXISTS end_notified_at TIMESTAMPTZ NULL")
    op.execute("ALTER TABLE work_shift_days ADD COLUMN IF NOT EXISTS end_snooze_until TIMESTAMPTZ NULL")
    op.execute("ALTER TABLE work_shift_days ADD COLUMN IF NOT EXISTS end_followup_notified_at TIMESTAMPTZ NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE work_shift_days DROP COLUMN IF EXISTS end_followup_notified_at")
    op.execute("ALTER TABLE work_shift_days DROP COLUMN IF EXISTS end_snooze_until")
    op.execute("ALTER TABLE work_shift_days DROP COLUMN IF EXISTS end_notified_at")
    op.execute("ALTER TABLE work_shift_days DROP COLUMN IF EXISTS start_notified_at")
