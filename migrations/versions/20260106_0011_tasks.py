"""tasks

Revision ID: 20260106_0011
Revises: 20251230_0010
Create Date: 2026-01-06

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM

from shared.enums import TaskStatus, TaskPriority, TaskEventType


# revision identifiers, used by Alembic.
revision = "20260106_0011"
down_revision = "20251230_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    task_status_enum = ENUM(
        TaskStatus,
        name="task_status_enum",
        create_type=False,
        values_callable=lambda obj: [e.value for e in obj],
    )
    task_priority_enum = ENUM(
        TaskPriority,
        name="task_priority_enum",
        create_type=False,
        values_callable=lambda obj: [e.value for e in obj],
    )
    task_event_type_enum = ENUM(
        TaskEventType,
        name="task_event_type_enum",
        create_type=False,
        values_callable=lambda obj: [e.value for e in obj],
    )

    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE task_status_enum AS ENUM ('new','in_progress','review','done','archived');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE task_priority_enum AS ENUM ('normal','urgent');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE task_event_type_enum AS ENUM (
                'created',
                'assigned_added',
                'assigned_removed',
                'status_changed',
                'comment_added',
                'archived',
                'unarchived'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", task_status_enum, nullable=False, server_default=TaskStatus.NEW.value),
        sa.Column(
            "priority",
            task_priority_enum,
            nullable=False,
            server_default=TaskPriority.NORMAL.value,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "completed_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_tasks_status", "tasks", ["status"], unique=False)
    op.create_index("ix_tasks_priority", "tasks", ["priority"], unique=False)
    op.create_index("ix_tasks_due_at", "tasks", ["due_at"], unique=False)
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"], unique=False)
    op.create_index("ix_tasks_archived_at", "tasks", ["archived_at"], unique=False)

    op.create_table(
        "task_assignees",
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("task_id", "user_id"),
        sa.UniqueConstraint("task_id", "user_id", name="uq_task_assignees_task_id_user_id"),
    )
    op.create_index("ix_task_assignees_user_id", "task_assignees", ["user_id"], unique=False)

    op.create_table(
        "task_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "author_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_comments_task_id", "task_comments", ["task_id"], unique=False)
    op.create_index("ix_task_comments_created_at", "task_comments", ["created_at"], unique=False)

    op.create_table(
        "task_comment_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "comment_id",
            sa.Integer(),
            sa.ForeignKey("task_comments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tg_file_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_task_comment_photos_comment_id", "task_comment_photos", ["comment_id"], unique=False)

    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("type", task_event_type_enum, nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"], unique=False)
    op.create_index("ix_task_events_created_at", "task_events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_task_events_created_at", table_name="task_events")
    op.drop_index("ix_task_events_task_id", table_name="task_events")
    op.drop_table("task_events")

    op.drop_index("ix_task_comment_photos_comment_id", table_name="task_comment_photos")
    op.drop_table("task_comment_photos")

    op.drop_index("ix_task_comments_created_at", table_name="task_comments")
    op.drop_index("ix_task_comments_task_id", table_name="task_comments")
    op.drop_table("task_comments")

    op.drop_index("ix_task_assignees_user_id", table_name="task_assignees")
    op.drop_table("task_assignees")

    op.drop_index("ix_tasks_archived_at", table_name="tasks")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_due_at", table_name="tasks")
    op.drop_index("ix_tasks_priority", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_table("tasks")

    op.execute("DROP TYPE IF EXISTS task_event_type_enum CASCADE")
    op.execute("DROP TYPE IF EXISTS task_priority_enum CASCADE")
    op.execute("DROP TYPE IF EXISTS task_status_enum CASCADE")
