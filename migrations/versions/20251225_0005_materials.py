"""
Alembic migration for materials and stock tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20251225_0005'
down_revision = '20251221_0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'material_types',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    op.create_table(
        'materials',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('material_type_id', sa.Integer(), sa.ForeignKey('material_types.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('short_name', sa.String(length=100), nullable=True),
        sa.Column('unit', sa.String(length=10), nullable=False, server_default='кг'),
        sa.Column('current_stock', sa.Numeric(16, 3), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_materials_material_type_id', 'materials', ['material_type_id'])

    op.create_table(
        'material_consumptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('material_id', sa.Integer(), sa.ForeignKey('materials.id', ondelete='CASCADE'), nullable=False),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('amount', sa.Numeric(16, 3), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_material_consumptions_material_id', 'material_consumptions', ['material_id'])
    op.create_index('ix_material_consumptions_date', 'material_consumptions', ['date'])

    op.create_table(
        'material_supplies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('material_id', sa.Integer(), sa.ForeignKey('materials.id', ondelete='CASCADE'), nullable=False),
        sa.Column('employee_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('amount', sa.Numeric(16, 3), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_material_supplies_material_id', 'material_supplies', ['material_id'])
    op.create_index('ix_material_supplies_date', 'material_supplies', ['date'])


def downgrade() -> None:
    op.drop_index('ix_material_supplies_date', table_name='material_supplies')
    op.drop_index('ix_material_supplies_material_id', table_name='material_supplies')
    op.drop_table('material_supplies')

    op.drop_index('ix_material_consumptions_date', table_name='material_consumptions')
    op.drop_index('ix_material_consumptions_material_id', table_name='material_consumptions')
    op.drop_table('material_consumptions')

    op.drop_index('ix_materials_material_type_id', table_name='materials')
    op.drop_table('materials')

    op.drop_table('material_types')
