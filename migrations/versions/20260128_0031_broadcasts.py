"""broadcasts: add broadcast history, deliveries, and ratings

Revision ID: 20260128_0031
Revises: 20260126_0030
Create Date: 2026-01-28

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260128_0031"
down_revision = "20260126_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_mode", sa.String(length=32), nullable=False, server_default=sa.text("'all'")),
        sa.Column("filter_positions", sa.JSON(), nullable=True),
        sa.Column("cta_label", sa.String(length=128), nullable=True),
        sa.Column("cta_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'draft'")),
    )

    op.create_index("ix_broadcasts_created_at", "broadcasts", ["created_at"], unique=False)
    op.create_index("ix_broadcasts_sent_at", "broadcasts", ["sent_at"], unique=False)
    op.create_index("ix_broadcasts_sent_by_user_id", "broadcasts", ["sent_by_user_id"], unique=False)

    op.alter_column("broadcasts", "target_mode", server_default=None)
    op.alter_column("broadcasts", "status", server_default=None)

    op.create_table(
        "broadcast_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("broadcast_id", sa.Integer(), sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tg_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("tg_message_id", sa.BigInteger(), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_status", sa.String(length=32), nullable=False, server_default=sa.text("'no_tg'")),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_deliveries_broadcast_user"),
    )

    op.create_index("ix_broadcast_deliveries_broadcast_id", "broadcast_deliveries", ["broadcast_id"], unique=False)
    op.create_index("ix_broadcast_deliveries_user_id", "broadcast_deliveries", ["user_id"], unique=False)
    op.create_index("ix_broadcast_deliveries_status", "broadcast_deliveries", ["delivery_status"], unique=False)
    op.create_index(
        "ix_broadcast_deliveries_broadcast_status",
        "broadcast_deliveries",
        ["broadcast_id", "delivery_status"],
        unique=False,
    )

    op.alter_column("broadcast_deliveries", "delivery_status", server_default=None)

    op.create_table(
        "broadcast_ratings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("broadcast_id", sa.Integer(), sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("rated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_ratings_broadcast_user"),
    )

    op.create_index("ix_broadcast_ratings_broadcast_id", "broadcast_ratings", ["broadcast_id"], unique=False)
    op.create_index("ix_broadcast_ratings_user_id", "broadcast_ratings", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_broadcast_ratings_user_id", table_name="broadcast_ratings")
    op.drop_index("ix_broadcast_ratings_broadcast_id", table_name="broadcast_ratings")
    op.drop_table("broadcast_ratings")

    op.drop_index("ix_broadcast_deliveries_broadcast_status", table_name="broadcast_deliveries")
    op.drop_index("ix_broadcast_deliveries_status", table_name="broadcast_deliveries")
    op.drop_index("ix_broadcast_deliveries_user_id", table_name="broadcast_deliveries")
    op.drop_index("ix_broadcast_deliveries_broadcast_id", table_name="broadcast_deliveries")
    op.drop_table("broadcast_deliveries")

    op.drop_index("ix_broadcasts_sent_by_user_id", table_name="broadcasts")
    op.drop_index("ix_broadcasts_sent_at", table_name="broadcasts")
    op.drop_index("ix_broadcasts_created_at", table_name="broadcasts")
    op.drop_table("broadcasts")
