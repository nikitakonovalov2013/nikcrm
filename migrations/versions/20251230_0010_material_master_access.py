"""material master access

Revision ID: 20251230_0010
Revises: 20251230_0009
Create Date: 2025-12-30

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251230_0010"
down_revision = "20251230_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "material_master_access",
        sa.Column("material_id", sa.Integer(), sa.ForeignKey("materials.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("material_id", "user_id"),
    )
    op.create_index("ix_material_master_access_material_id", "material_master_access", ["material_id"], unique=False)
    op.create_index("ix_material_master_access_user_id", "material_master_access", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_material_master_access_user_id", table_name="material_master_access")
    op.drop_index("ix_material_master_access_material_id", table_name="material_master_access")
    op.drop_table("material_master_access")
