"""salary audit tables

Revision ID: 20260216_0039
Revises: 20260213_0038
Create Date: 2026-02-16

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260216_0039"
down_revision = "20260213_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "salary_shift_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("shift_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_salary_shift_audit_shift_id", "salary_shift_audit", ["shift_id"], unique=False)
    op.create_index("ix_salary_shift_audit_shift_created_at", "salary_shift_audit", ["shift_id", "created_at"], unique=False)
    op.create_index("ix_salary_shift_audit_actor_user_id", "salary_shift_audit", ["actor_user_id"], unique=False)

    op.create_table(
        "salary_payout_audit",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payout_id", sa.Integer(), sa.ForeignKey("salary_payouts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_salary_payout_audit_user_id", "salary_payout_audit", ["user_id"], unique=False)
    op.create_index("ix_salary_payout_audit_user_created_at", "salary_payout_audit", ["user_id", "created_at"], unique=False)
    op.create_index("ix_salary_payout_audit_payout_id", "salary_payout_audit", ["payout_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_salary_payout_audit_payout_id", table_name="salary_payout_audit")
    op.drop_index("ix_salary_payout_audit_user_created_at", table_name="salary_payout_audit")
    op.drop_index("ix_salary_payout_audit_user_id", table_name="salary_payout_audit")
    op.drop_table("salary_payout_audit")

    op.drop_index("ix_salary_shift_audit_actor_user_id", table_name="salary_shift_audit")
    op.drop_index("ix_salary_shift_audit_shift_created_at", table_name="salary_shift_audit")
    op.drop_index("ix_salary_shift_audit_shift_id", table_name="salary_shift_audit")
    op.drop_table("salary_shift_audit")
