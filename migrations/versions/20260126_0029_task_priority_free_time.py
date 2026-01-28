"""tasks: add free_time to priority enum

Revision ID: 20260126_0029
Revises: 20260126_0028
Create Date: 2026-01-26

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260126_0029"
down_revision = "20260126_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum ADD VALUE must be committed before the value can be used.
    # In this project env, autocommit_block() is not available, so we force commit.
    op.execute("COMMIT")
    op.execute("ALTER TYPE task_priority_enum ADD VALUE IF NOT EXISTS 'free_time'")
    op.execute("COMMIT")


def downgrade() -> None:
    # No-op: PostgreSQL does not support removing enum values.
    pass
