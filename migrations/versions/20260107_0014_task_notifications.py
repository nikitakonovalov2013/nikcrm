"""task notifications

Revision ID: 20260107_0014
Revises: 20260107_0013
Create Date: 2026-01-07

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260107_0014"
down_revision = "20260107_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_index("ix_task_notifications_task_id", "task_notifications", ["task_id"], unique=False)
    op.create_index("ix_task_notifications_recipient_user_id", "task_notifications", ["recipient_user_id"], unique=False)
    op.create_index("ix_task_notifications_status", "task_notifications", ["status"], unique=False)
    op.create_index("ix_task_notifications_scheduled_at", "task_notifications", ["scheduled_at"], unique=False)

    op.create_index(
        "ix_task_notifications_status_scheduled_at",
        "task_notifications",
        ["status", "scheduled_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_notifications_recipient_status",
        "task_notifications",
        ["recipient_user_id", "status"],
        unique=False,
    )

    op.create_unique_constraint(
        "uq_task_notifications_recipient_dedupe",
        "task_notifications",
        ["recipient_user_id", "dedupe_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_task_notifications_recipient_dedupe", "task_notifications", type_="unique")
    op.drop_index("ix_task_notifications_recipient_status", table_name="task_notifications")
    op.drop_index("ix_task_notifications_status_scheduled_at", table_name="task_notifications")
    op.drop_index("ix_task_notifications_scheduled_at", table_name="task_notifications")
    op.drop_index("ix_task_notifications_status", table_name="task_notifications")
    op.drop_index("ix_task_notifications_recipient_user_id", table_name="task_notifications")
    op.drop_index("ix_task_notifications_task_id", table_name="task_notifications")
    op.drop_table("task_notifications")
