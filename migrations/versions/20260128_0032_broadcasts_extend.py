"""broadcasts: extend with media, recipient filters and sending progress

Revision ID: 20260128_0032
Revises: 20260128_0031
Create Date: 2026-01-28

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260128_0032"
down_revision = "20260128_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("broadcasts", sa.Column("filter_user_ids", sa.JSON(), nullable=True))

    op.add_column("broadcasts", sa.Column("total_recipients", sa.Integer(), nullable=True))
    op.add_column("broadcasts", sa.Column("delivered_count", sa.Integer(), nullable=True))
    op.add_column("broadcasts", sa.Column("failed_count", sa.Integer(), nullable=True))
    op.add_column("broadcasts", sa.Column("no_tg_count", sa.Integer(), nullable=True))

    op.add_column("broadcasts", sa.Column("media_type", sa.String(length=16), nullable=True))
    op.add_column("broadcasts", sa.Column("media_path", sa.Text(), nullable=True))
    op.add_column("broadcasts", sa.Column("media_url", sa.Text(), nullable=True))
    op.add_column("broadcasts", sa.Column("tg_file_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("broadcasts", "tg_file_id")
    op.drop_column("broadcasts", "media_url")
    op.drop_column("broadcasts", "media_path")
    op.drop_column("broadcasts", "media_type")

    op.drop_column("broadcasts", "no_tg_count")
    op.drop_column("broadcasts", "failed_count")
    op.drop_column("broadcasts", "delivered_count")
    op.drop_column("broadcasts", "total_recipients")

    op.drop_column("broadcasts", "filter_user_ids")
