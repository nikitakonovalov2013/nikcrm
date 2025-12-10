"""init tables

Revision ID: 20251210_0001
Revises: 
Create Date: 2025-12-10 13:20:00.000000
"""
from alembic import op
import sqlalchemy as sa
from shared.enums import UserStatus, Schedule, Position, AdminActionType

# revision identifiers, used by Alembic.
revision = "20251210_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tg_id", sa.Integer(), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("rate_k", sa.Integer(), nullable=True),
        sa.Column("schedule", sa.Enum(Schedule, name="schedule"), nullable=True),
        sa.Column("position", sa.Enum(Position, name="position"), nullable=True),
        sa.Column("status", sa.Enum(UserStatus, name="user_status"), nullable=False, server_default=UserStatus.PENDING.value),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("tg_id", name="uq_users_tg_id"),
    )
    op.create_index("ix_tg_id", "users", ["tg_id"]) 

    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_tg_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.Enum(AdminActionType, name="admin_action_type"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_admin_tg_id", "admin_actions", ["admin_tg_id"]) 


def downgrade() -> None:
    op.drop_index("ix_admin_tg_id", table_name="admin_actions")
    op.drop_table("admin_actions")

    op.drop_index("ix_tg_id", table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE IF EXISTS schedule CASCADE")
    op.execute("DROP TYPE IF EXISTS position CASCADE")
    op.execute("DROP TYPE IF EXISTS user_status CASCADE")
    op.execute("DROP TYPE IF EXISTS admin_action_type CASCADE")
