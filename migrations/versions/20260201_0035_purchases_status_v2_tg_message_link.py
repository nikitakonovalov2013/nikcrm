"""purchases: migrate statuses to NEW/IN_PROGRESS/BOUGHT/CANCELED and add tg message link fields

Revision ID: 20260201_0035
Revises: 20260130_0034
Create Date: 2026-02-01

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260201_0035"
down_revision = "20260130_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Add TG message link fields (chat/message id) for WEB<->BOT sync
    op.add_column("purchases", sa.Column("tg_chat_id", sa.BigInteger(), nullable=True))
    op.add_column("purchases", sa.Column("tg_message_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_purchases_tg_chat_id", "purchases", ["tg_chat_id"])

    # 2) Replace enum type purchase_status_enum with new values
    # Old: PENDING/DONE/REJECTED
    # New: NEW/IN_PROGRESS/BOUGHT/CANCELED

    # Drop default before type change
    op.execute("ALTER TABLE purchases ALTER COLUMN status DROP DEFAULT")

    # Rename old enum type
    op.execute("ALTER TYPE purchase_status_enum RENAME TO purchase_status_enum_old")

    # Create new enum type
    op.execute("CREATE TYPE purchase_status_enum AS ENUM ('NEW','IN_PROGRESS','BOUGHT','CANCELED')")

    # Convert column values
    op.execute(
        """
        ALTER TABLE purchases
        ALTER COLUMN status TYPE purchase_status_enum
        USING (
            CASE status::text
                WHEN 'PENDING' THEN 'NEW'::purchase_status_enum
                WHEN 'DONE' THEN 'BOUGHT'::purchase_status_enum
                WHEN 'REJECTED' THEN 'CANCELED'::purchase_status_enum
                ELSE 'NEW'::purchase_status_enum
            END
        )
        """
    )

    # Restore default
    op.execute("ALTER TABLE purchases ALTER COLUMN status SET DEFAULT 'NEW'")

    # Drop old enum type
    op.execute("DROP TYPE purchase_status_enum_old")


def downgrade() -> None:
    # Re-create old enum and convert back.
    op.execute("ALTER TABLE purchases ALTER COLUMN status DROP DEFAULT")

    op.execute("ALTER TYPE purchase_status_enum RENAME TO purchase_status_enum_new")
    op.execute("CREATE TYPE purchase_status_enum AS ENUM ('PENDING','DONE','REJECTED')")

    op.execute(
        """
        ALTER TABLE purchases
        ALTER COLUMN status TYPE purchase_status_enum
        USING (
            CASE status::text
                WHEN 'NEW' THEN 'PENDING'::purchase_status_enum
                WHEN 'IN_PROGRESS' THEN 'PENDING'::purchase_status_enum
                WHEN 'BOUGHT' THEN 'DONE'::purchase_status_enum
                WHEN 'CANCELED' THEN 'REJECTED'::purchase_status_enum
                ELSE 'PENDING'::purchase_status_enum
            END
        )
        """
    )

    op.execute("ALTER TABLE purchases ALTER COLUMN status SET DEFAULT 'PENDING'")

    op.execute("DROP TYPE purchase_status_enum_new")

    op.drop_index("ix_purchases_tg_chat_id", table_name="purchases")
    op.drop_column("purchases", "tg_message_id")
    op.drop_column("purchases", "tg_chat_id")
