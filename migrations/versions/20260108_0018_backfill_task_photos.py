"""backfill task photo_url and tg_photo_file_id

Revision ID: 20260108_0018
Revises: 20260108_0017
Create Date: 2026-01-08

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20260108_0018"
down_revision = "20260108_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fill canonical fields from legacy ones for existing rows.
    # We store photo_url as the same relative path as photo_path;
    # bot/web clients can convert it to absolute via PUBLIC_BASE_URL.
    op.execute(
        """
        UPDATE tasks
        SET photo_url = photo_path
        WHERE photo_url IS NULL AND photo_path IS NOT NULL;
        """
    )
    op.execute(
        """
        UPDATE tasks
        SET tg_photo_file_id = photo_file_id
        WHERE tg_photo_file_id IS NULL AND photo_file_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    # No-op: do not try to unset user data.
    pass
