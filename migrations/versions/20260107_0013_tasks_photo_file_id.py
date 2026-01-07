"""tasks photo_file_id

Revision ID: 20260107_0013
Revises: 20260106_0012
Create Date: 2026-01-07

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260107_0013"
down_revision = "20260106_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("photo_file_id", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "photo_file_id")
