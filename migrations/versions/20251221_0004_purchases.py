"""add purchases table

Revision ID: 20251221_0004
Revises: 20251215_0003
Create Date: 2025-12-21 19:30:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM
from shared.enums import PurchaseStatus

# revision identifiers, used by Alembic.
revision = "20251221_0004"
down_revision = "20251215_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    purchase_status_enum = ENUM(
        PurchaseStatus,
        name="purchase_status_enum",
        create_type=False,
        values_callable=lambda obj: [e.value for e in obj],
    )

    # Create enum type if not exists
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE purchase_status_enum AS ENUM ('PENDING','DONE','REJECTED');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )

    op.create_table(
        "purchases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text", sa.String(length=2000), nullable=False),
        sa.Column("status", purchase_status_enum, nullable=False, server_default=PurchaseStatus.PENDING.value),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("purchases")
    op.execute("DROP TYPE IF EXISTS purchase_status_enum CASCADE")
