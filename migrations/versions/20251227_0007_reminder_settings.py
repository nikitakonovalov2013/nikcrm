"""reminder settings

Revision ID: 20251227_0007
Revises: 20251226_0006
Create Date: 2025-12-27 00:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20251227_0007"
down_revision = "20251226_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reminder_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("reminder_time", sa.Time(), nullable=False, server_default=sa.text("'16:00:00'")),
        sa.Column("skip_weekends", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("send_to_admins", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("send_to_managers", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("daily_report_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("daily_report_time", sa.Time(), nullable=False, server_default=sa.text("'18:00:00'")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.execute(
        """
        INSERT INTO reminder_settings (id) VALUES (1)
        ON CONFLICT (id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("reminder_settings")
