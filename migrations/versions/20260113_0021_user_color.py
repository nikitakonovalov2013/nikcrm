"""users: add color

Revision ID: 20260113_0021
Revises: 20260113_0020
Create Date: 2026-01-13

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260113_0021"
down_revision = "20260113_0020"
branch_labels = None
depends_on = None


_PALETTE = [
    "#EF4444",  # red
    "#F97316",  # orange
    "#F59E0B",  # amber
    "#84CC16",  # lime
    "#22C55E",  # green
    "#10B981",  # emerald
    "#14B8A6",  # teal
    "#06B6D4",  # cyan
    "#0EA5E9",  # sky
    "#3B82F6",  # blue
    "#6366F1",  # indigo
    "#8B5CF6",  # violet
    "#A855F7",  # purple
    "#D946EF",  # fuchsia
    "#EC4899",  # pink
    "#F43F5E",  # rose
    "#64748B",  # slate
    "#0F766E",  # teal dark
    "#B45309",  # amber dark
    "#4D7C0F",  # lime dark
]


def upgrade() -> None:
    op.add_column("users", sa.Column("color", sa.String(length=7), nullable=True))

    # Backfill deterministically by user id order, only for users without explicit color.
    # Use palette sequentially; after palette ends, cycle.
    values_sql = ",".join([f"('{c}')" for c in _PALETTE])
    op.execute(
        f"""
        WITH palette AS (
            SELECT row_number() OVER () - 1 AS idx, v.color
            FROM (VALUES {values_sql}) AS v(color)
        ), targets AS (
            SELECT id, row_number() OVER (ORDER BY id) - 1 AS rn
            FROM users
            WHERE color IS NULL
        )
        UPDATE users u
        SET color = (
            SELECT p.color
            FROM palette p
            WHERE p.idx = (targets.rn % {len(_PALETTE)})
        )
        FROM targets
        WHERE u.id = targets.id;
        """
    )

    op.alter_column("users", "color", existing_type=sa.String(length=7), nullable=False)


def downgrade() -> None:
    op.drop_column("users", "color")
