"""finance: add finance module tables

Revision ID: 20260316_0050
Revises: 20260311_0049
Create Date: 2026-03-16

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260316_0050"
down_revision = "20260311_0049"
branch_labels = None
depends_on = None

EXPENSE_CATEGORIES = [
    "Материалы",
    "Зарплаты",
    "Закупки/поставщики",
    "Логистика",
    "Маркетплейсы: Комиссии",
    "Маркетплейсы: Реклама",
    "Маркетплейсы: Штрафы",
    "Аренда/Коммуналка",
    "Оборудование и ремонт",
    "Расходники/хозтовары",
    "Сервисы/подписки",
    "Налоги/платежи",
    "Прочее",
]

INCOME_CATEGORIES = [
    "Продажи Wildberries",
    "Продажи Ozon",
    "Прямые продажи",
    "Прочие доходы",
]


def upgrade() -> None:
    op.create_table(
        "finance_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pin_hash", sa.String(256), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )

    op.create_table(
        "finance_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.UniqueConstraint("type", "name", name="uq_finance_categories_type_name"),
    )
    op.create_index("ix_finance_categories_type", "finance_categories", ["type"])
    op.create_index("ix_finance_categories_is_archived", "finance_categories", ["is_archived"])

    op.create_table(
        "finance_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("finance_categories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("subcategory", sa.String(200), nullable=True),
        sa.Column("project", sa.String(100), nullable=True),
        sa.Column("counterparty", sa.String(300), nullable=True),
        sa.Column("payment_method", sa.String(100), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_finance_operations_type", "finance_operations", ["type"])
    op.create_index("ix_finance_operations_occurred_at", "finance_operations", ["occurred_at"])
    op.create_index("ix_finance_operations_project", "finance_operations", ["project"])
    op.create_index("ix_finance_operations_created_at", "finance_operations", ["created_at"])
    op.create_index("ix_finance_operations_category_id", "finance_operations", ["category_id"])

    op.create_table(
        "finance_operation_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_id", sa.Integer(), sa.ForeignKey("finance_operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_url", sa.Text(), nullable=True),
        sa.Column("tg_file_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_finance_operation_files_operation_id", "finance_operation_files", ["operation_id"])

    conn = op.get_bind()
    import hashlib
    raw = "nikcrm_finance_pin_v1:000000".encode("utf-8")
    pin_hash = hashlib.sha256(raw).hexdigest()
    conn.execute(sa.text("INSERT INTO finance_settings (id, pin_hash) VALUES (1, :h) ON CONFLICT DO NOTHING"), {"h": pin_hash})

    now = sa.text("NOW()")
    for name in EXPENSE_CATEGORIES:
        conn.execute(
            sa.text("INSERT INTO finance_categories (type, name) VALUES ('expense', :n) ON CONFLICT DO NOTHING"),
            {"n": name},
        )
    for name in INCOME_CATEGORIES:
        conn.execute(
            sa.text("INSERT INTO finance_categories (type, name) VALUES ('income', :n) ON CONFLICT DO NOTHING"),
            {"n": name},
        )


def downgrade() -> None:
    op.drop_table("finance_operation_files")
    op.drop_index("ix_finance_operations_category_id", table_name="finance_operations")
    op.drop_index("ix_finance_operations_created_at", table_name="finance_operations")
    op.drop_index("ix_finance_operations_project", table_name="finance_operations")
    op.drop_index("ix_finance_operations_occurred_at", table_name="finance_operations")
    op.drop_index("ix_finance_operations_type", table_name="finance_operations")
    op.drop_table("finance_operations")
    op.drop_index("ix_finance_categories_is_archived", table_name="finance_categories")
    op.drop_index("ix_finance_categories_type", table_name="finance_categories")
    op.drop_table("finance_categories")
    op.drop_table("finance_settings")
