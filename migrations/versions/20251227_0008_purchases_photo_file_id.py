"""add purchases photo_file_id

Revision ID: 20251227_0008
Revises: 20251227_0007
Create Date: 2025-12-27 23:59:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251227_0008"
down_revision = "20251227_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchases", sa.Column("photo_file_id", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("purchases", "photo_file_id")
