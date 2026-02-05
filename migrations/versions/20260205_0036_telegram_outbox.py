"""telegram outbox

Revision ID: 20260205_0036
Revises: 20260201_0035
Create Date: 2026-02-05

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260205_0036"
down_revision = "20260201_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_telegram_outbox_kind", "telegram_outbox", ["kind"])
    op.create_index("ix_telegram_outbox_status", "telegram_outbox", ["status"])
    op.create_index("ix_telegram_outbox_next_retry_at", "telegram_outbox", ["next_retry_at"])
    op.create_index("ix_telegram_outbox_status_next_retry_at", "telegram_outbox", ["status", "next_retry_at"])


def downgrade() -> None:
    op.drop_index("ix_telegram_outbox_status_next_retry_at", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_next_retry_at", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_status", table_name="telegram_outbox")
    op.drop_index("ix_telegram_outbox_kind", table_name="telegram_outbox")
    op.drop_table("telegram_outbox")
