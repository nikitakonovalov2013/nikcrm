"""task events: add edited type

Revision ID: 20260113_0020
Revises: 20260108_0019
Create Date: 2026-01-13

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260113_0020"
down_revision = "20260108_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
            ALTER TYPE task_event_type_enum ADD VALUE 'edited';
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def downgrade() -> None:
    # Postgres enums cannot easily remove values; leave as-is.
    pass
