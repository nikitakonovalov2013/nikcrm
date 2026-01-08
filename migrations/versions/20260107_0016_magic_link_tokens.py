"""magic link tokens

Revision ID: 20260107_0016
Revises: 20260107_0015
Create Date: 2026-01-07

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260107_0016"
down_revision = "20260107_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "magic_link_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_magic_link_tokens_token"), "magic_link_tokens", ["token"], unique=True)
    op.create_index(op.f("ix_magic_link_tokens_user_id"), "magic_link_tokens", ["user_id"], unique=False)
    op.create_index(op.f("ix_magic_link_tokens_expires_at"), "magic_link_tokens", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_magic_link_tokens_expires_at"), table_name="magic_link_tokens")
    op.drop_index(op.f("ix_magic_link_tokens_user_id"), table_name="magic_link_tokens")
    op.drop_index(op.f("ix_magic_link_tokens_token"), table_name="magic_link_tokens")
    op.drop_table("magic_link_tokens")
