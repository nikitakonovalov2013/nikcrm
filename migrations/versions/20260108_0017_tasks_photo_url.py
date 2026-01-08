"""tasks photo_url and tg_photo_file_id

Revision ID: 20260108_0017
Revises: 20260107_0016
Create Date: 2026-01-08

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260108_0017"
down_revision = "20260107_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("photo_url", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("tg_photo_file_id", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "tg_photo_file_id")
    op.drop_column("tasks", "photo_url")
