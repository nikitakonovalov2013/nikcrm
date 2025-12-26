"""Add unique constraint for materials.name

Revision ID: 20251226_0006
Revises: 20251225_0005
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20251226_0006"
down_revision = "20251225_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_materials_name", "materials", ["name"])


def downgrade() -> None:
    op.drop_constraint("uq_materials_name", "materials", type_="unique")
