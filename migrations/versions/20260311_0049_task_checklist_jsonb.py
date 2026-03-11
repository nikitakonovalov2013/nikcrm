"""task checklist jsonb

Revision ID: 20260311_0049
Revises: 20260309_0048
Create Date: 2026-03-11

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260311_0049"
down_revision = "20260309_0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("checklist", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "checklist")
