"""users soft delete

Revision ID: 20251230_0009
Revises: 20251227_0008
Create Date: 2025-12-30

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251230_0009"
down_revision = "20251227_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.create_index("ix_users_is_deleted", "users", ["is_deleted"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_is_deleted", table_name="users")
    op.drop_column("users", "is_deleted")
