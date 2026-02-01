"""purchases: take in work fields and events

Revision ID: 20260130_0033
Revises: 20260128_0032
Create Date: 2026-01-30

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260130_0033"
down_revision = "20260128_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchases", sa.Column("taken_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("purchases", sa.Column("priority", sa.String(length=32), nullable=True))
    op.add_column("purchases", sa.Column("description", sa.Text(), nullable=True))

    op.create_index("ix_purchases_taken_by_user_id", "purchases", ["taken_by_user_id"])
    op.create_index("ix_purchases_status", "purchases", ["status"])

    op.create_table(
        "purchase_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purchase_id", sa.Integer(), sa.ForeignKey("purchases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_index("ix_purchase_events_purchase_id", "purchase_events", ["purchase_id"])
    op.create_index("ix_purchase_events_created_at", "purchase_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_purchase_events_created_at", table_name="purchase_events")
    op.drop_index("ix_purchase_events_purchase_id", table_name="purchase_events")
    op.drop_table("purchase_events")

    op.drop_index("ix_purchases_status", table_name="purchases")
    op.drop_index("ix_purchases_taken_by_user_id", table_name="purchases")

    op.drop_column("purchases", "description")
    op.drop_column("purchases", "priority")
    op.drop_column("purchases", "taken_by_user_id")
