"""tasks: migrate task_type free_time -> priority free_time and cleanup

Revision ID: 20260126_0030
Revises: 20260126_0029
Create Date: 2026-01-26

"""

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = "20260126_0030"
down_revision = "20260126_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    has_enum = conn.execute(
        text(
            """
            SELECT 1
            FROM pg_enum e
            JOIN pg_type t ON t.oid = e.enumtypid
            WHERE t.typname = 'task_priority_enum' AND e.enumlabel = 'free_time'
            LIMIT 1
            """
        )
    ).first()
    if has_enum is None:
        raise RuntimeError("Migration error: task_priority_enum is missing value 'free_time' (0029 must be committed first)")

    has_col = conn.execute(
        text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'tasks' AND column_name = 'task_type'
            LIMIT 1
            """
        )
    ).first()
    if has_col is None:
        return

    op.execute("UPDATE tasks SET priority = 'free_time' WHERE task_type::text = 'free_time'")
    op.execute("DROP INDEX IF EXISTS ix_tasks_task_type")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS task_type")
    op.execute("DROP TYPE IF EXISTS task_type_enum CASCADE")


def downgrade() -> None:
    # No-op: not safely reversible.
    pass
