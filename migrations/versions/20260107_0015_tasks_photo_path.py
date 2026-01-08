"""tasks photo_path

Revision ID: 20260107_0015
Revises: 20260107_0014
Create Date: 2026-01-07

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260107_0015"
down_revision = "20260107_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("photo_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "photo_path")
