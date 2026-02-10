"""add designer position

Revision ID: 20260210_0037
Revises: 20260205_0036
Create Date: 2026-02-10

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260210_0037"
down_revision = "20260205_0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres enum update: add new Russian label used by shared.enums.Position
    # Keep idempotent to avoid failing on repeated runs.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = 'user_position_enum'
                  AND e.enumlabel = 'Дизайнер'
            ) THEN
                ALTER TYPE user_position_enum ADD VALUE 'Дизайнер';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Enum value removal is not supported in PostgreSQL.
    pass
