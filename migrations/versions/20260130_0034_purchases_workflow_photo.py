"""purchases: workflow fields, approval, archive and web photo

Revision ID: 20260130_0034
Revises: 20260130_0033
Create Date: 2026-01-30

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260130_0034"
down_revision = "20260130_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchases", sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("purchases", sa.Column("bought_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("purchases", sa.Column("bought_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("purchases", sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("purchases", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("purchases", sa.Column("archived_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.add_column("purchases", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("purchases", sa.Column("photo_key", sa.Text(), nullable=True))
    op.add_column("purchases", sa.Column("photo_path", sa.Text(), nullable=True))
    op.add_column("purchases", sa.Column("photo_url", sa.Text(), nullable=True))
    op.add_column("purchases", sa.Column("tg_photo_file_id", sa.String(length=512), nullable=True))

    op.create_index("ix_purchases_archived_at", "purchases", ["archived_at"])
    op.create_index("ix_purchases_bought_at", "purchases", ["bought_at"])
    op.create_index("ix_purchases_taken_at", "purchases", ["taken_at"])


def downgrade() -> None:
    op.drop_index("ix_purchases_taken_at", table_name="purchases")
    op.drop_index("ix_purchases_bought_at", table_name="purchases")
    op.drop_index("ix_purchases_archived_at", table_name="purchases")

    op.drop_column("purchases", "tg_photo_file_id")
    op.drop_column("purchases", "photo_url")
    op.drop_column("purchases", "photo_path")
    op.drop_column("purchases", "photo_key")

    op.drop_column("purchases", "archived_at")
    op.drop_column("purchases", "archived_by_user_id")

    op.drop_column("purchases", "approved_at")
    op.drop_column("purchases", "approved_by_user_id")

    op.drop_column("purchases", "bought_at")
    op.drop_column("purchases", "bought_by_user_id")

    op.drop_column("purchases", "taken_at")
