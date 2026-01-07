"""tasks started_by

Revision ID: 20260106_0012
Revises: 20260106_0011
Create Date: 2026-01-06

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260106_0012"
down_revision = "20260106_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("started_by_user_id", sa.Integer(), nullable=True))
    op.add_column("tasks", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))

    op.create_foreign_key(
        "fk_tasks_started_by_user_id_users",
        "tasks",
        "users",
        ["started_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_tasks_started_by_user_id", "tasks", ["started_by_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tasks_started_by_user_id", table_name="tasks")
    op.drop_constraint("fk_tasks_started_by_user_id_users", "tasks", type_="foreignkey")

    op.drop_column("tasks", "started_at")
    op.drop_column("tasks", "started_by_user_id")
