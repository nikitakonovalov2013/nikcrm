"""shift rating fields

Revision ID: 20260309_0048
Revises: 20260302_0047
Create Date: 2026-03-09

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260309_0048"
down_revision = "20260302_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shift_instances", sa.Column("rating", sa.Integer(), nullable=True))
    op.add_column("shift_instances", sa.Column("rated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("shift_instances", sa.Column("rating_message_id", sa.BigInteger(), nullable=True))
    op.add_column("shift_instances", sa.Column("rating_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_shift_instances_rating_range",
        "shift_instances",
        "rating IS NULL OR (rating >= 1 AND rating <= 5)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_shift_instances_rating_range", "shift_instances", type_="check")
    op.drop_column("shift_instances", "rating_requested_at")
    op.drop_column("shift_instances", "rating_message_id")
    op.drop_column("shift_instances", "rated_at")
    op.drop_column("shift_instances", "rating")
