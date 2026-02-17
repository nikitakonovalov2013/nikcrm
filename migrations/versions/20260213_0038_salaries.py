"""salaries: add salary settings, shift states, adjustments, payouts, and user hour_rate

Revision ID: 20260213_0038
Revises: 20260210_0037
Create Date: 2026-02-13

"""

from __future__ import annotations

import hashlib

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


# revision identifiers, used by Alembic.
revision = "20260213_0038"
down_revision = "20260210_0037"
branch_labels = None
depends_on = None


def _pin_hash_default(pin: str) -> str:
    # Must match shared Salaries PIN hashing implementation.
    # Keep deterministic for migrations.
    raw = f"nikcrm_salary_pin_v1:{str(pin)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def upgrade() -> None:
    # 1) users.hour_rate
    op.add_column("users", sa.Column("hour_rate", sa.Numeric(12, 2), nullable=True))

    # 2) salary_shift_state_enum
    salary_shift_state_enum = ENUM(
        "worked",
        "day_off",
        "overtime",
        "skip",
        "needs_review",
        name="salary_shift_state_enum",
        create_type=False,
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE salary_shift_state_enum AS ENUM (
                'worked',
                'day_off',
                'overtime',
                'skip',
                'needs_review'
            );
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )

    # 3) salary_settings
    op.create_table(
        "salary_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pin_hash", sa.String(length=256), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_table(
        "salary_shift_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("shift_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", salary_shift_state_enum, nullable=False, server_default=sa.text("'worked'")),
        sa.Column("manual_hours", sa.Numeric(12, 2), nullable=True),
        sa.Column("manual_amount_override", sa.Numeric(12, 2), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("shift_id", name="uq_salary_shift_state_shift_id"),
    )
    op.create_index("ix_salary_shift_state_shift_id", "salary_shift_state", ["shift_id"], unique=True)
    op.create_index("ix_salary_shift_state_state", "salary_shift_state", ["state"], unique=False)

    op.create_table(
        "salary_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("shift_id", sa.Integer(), sa.ForeignKey("shift_instances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("delta_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_salary_adjustments_shift_id", "salary_adjustments", ["shift_id"], unique=False)
    op.create_index(
        "ix_salary_adjustments_shift_created_at",
        "salary_adjustments",
        ["shift_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "salary_payouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_salary_payouts_user_id", "salary_payouts", ["user_id"], unique=False)
    op.create_index("ix_salary_payouts_user_created_at", "salary_payouts", ["user_id", "created_at"], unique=False)

    # seed one row with default PIN
    op.execute(
        sa.text("INSERT INTO salary_settings (id, pin_hash, updated_by_user_id) VALUES (1, :h, NULL) ON CONFLICT (id) DO NOTHING")
        .bindparams(h=_pin_hash_default("000000"))
    )

    # cleanup server defaults
    op.alter_column("salary_shift_state", "state", server_default=None)
    op.alter_column("salary_shift_state", "is_paid", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_salary_payouts_user_created_at", table_name="salary_payouts")
    op.drop_index("ix_salary_payouts_user_id", table_name="salary_payouts")
    op.drop_table("salary_payouts")

    op.drop_index("ix_salary_adjustments_shift_created_at", table_name="salary_adjustments")
    op.drop_index("ix_salary_adjustments_shift_id", table_name="salary_adjustments")
    op.drop_table("salary_adjustments")

    op.drop_index("ix_salary_shift_state_state", table_name="salary_shift_state")
    op.drop_index("ix_salary_shift_state_shift_id", table_name="salary_shift_state")
    op.drop_table("salary_shift_state")

    op.drop_table("salary_settings")

    op.drop_column("users", "hour_rate")

    op.execute("DROP TYPE IF EXISTS salary_shift_state_enum CASCADE")
