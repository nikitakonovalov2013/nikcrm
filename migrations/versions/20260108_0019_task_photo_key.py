"""task photo_key canonical storage

Revision ID: 20260108_0019
Revises: 20260108_0018
Create Date: 2026-01-08

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260108_0019"
down_revision = "20260108_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("photo_key", sa.Text(), nullable=True))

    # Backfill from existing fields:
    # - photo_path: usually '/crm/static/uploads/tasks/<name>'
    # - photo_url:  may be absolute URL containing '/crm/static/uploads/<...>'
    op.execute(
        """
        UPDATE tasks
        SET photo_key = regexp_replace(photo_path, '^/crm/static/uploads/', '')
        WHERE photo_key IS NULL
          AND photo_path IS NOT NULL
          AND photo_path LIKE '/crm/static/uploads/%';
        """
    )

    op.execute(
        """
        UPDATE tasks
        SET photo_key = regexp_replace(photo_url, '^.*?/crm/static/uploads/', '')
        WHERE photo_key IS NULL
          AND photo_url IS NOT NULL
          AND photo_url LIKE '%/crm/static/uploads/%';
        """
    )


def downgrade() -> None:
    op.drop_column("tasks", "photo_key")
