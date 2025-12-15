"""noop after enum/bigint fix

Revision ID: 20251210_0002
Revises: 20251210_0001
Create Date: 2025-12-10 14:40:00.000000
"""
# This migration is intentionally left blank because the initial migration
# now creates BIGINT columns and correct enum type names.

revision = "20251210_0002"
down_revision = "20251210_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
