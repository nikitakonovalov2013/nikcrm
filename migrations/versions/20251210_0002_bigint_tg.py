"""alter tg_id and admin_tg_id to BIGINT

Revision ID: 20251210_0002
Revises: 20251210_0001
Create Date: 2025-12-10 14:40:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251210_0002"
down_revision = "20251210_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("tg_id", type_=sa.BigInteger())
    with op.batch_alter_table("admin_actions") as batch_op:
        batch_op.alter_column("admin_tg_id", type_=sa.BigInteger())


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("tg_id", type_=sa.Integer())
    with op.batch_alter_table("admin_actions") as batch_op:
        batch_op.alter_column("admin_tg_id", type_=sa.Integer())
