"""finance: source refs for salary payout sync

Revision ID: 20260319_0051
Revises: 20260316_0050
Create Date: 2026-03-19

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260319_0051"
down_revision = "20260316_0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("finance_operations", sa.Column("source_type", sa.String(length=32), nullable=True))
    op.add_column("finance_operations", sa.Column("source_id", sa.Integer(), nullable=True))

    # Backfill new source link fields from legacy project key: salary_payout:<id>
    op.execute(
        """
        UPDATE finance_operations
        SET source_type = 'salary_payout',
            source_id = NULLIF(split_part(project, ':', 2), '')::integer
        WHERE source_type IS NULL
          AND source_id IS NULL
          AND project ~ '^salary_payout:[0-9]+$';
        """
    )

    # Deduplicate possible historical duplicates before adding unique constraint.
    op.execute(
        """
        DELETE FROM finance_operations f
        USING finance_operations d
        WHERE f.id > d.id
          AND f.source_type = d.source_type
          AND f.source_id = d.source_id
          AND f.source_type IS NOT NULL
          AND f.source_id IS NOT NULL;
        """
    )

    op.create_index("ix_finance_operations_source_type", "finance_operations", ["source_type"])
    op.create_index("ix_finance_operations_source_id", "finance_operations", ["source_id"])
    op.create_unique_constraint(
        "uq_finance_operations_source_type_source_id",
        "finance_operations",
        ["source_type", "source_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_finance_operations_source_type_source_id", "finance_operations", type_="unique")
    op.drop_index("ix_finance_operations_source_id", table_name="finance_operations")
    op.drop_index("ix_finance_operations_source_type", table_name="finance_operations")
    op.drop_column("finance_operations", "source_id")
    op.drop_column("finance_operations", "source_type")
