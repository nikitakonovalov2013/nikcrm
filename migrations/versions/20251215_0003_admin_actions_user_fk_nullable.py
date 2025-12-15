"""Make admin_actions.user_id nullable and set FK ON DELETE SET NULL

Revision ID: 20251215_0003
Revises: 20251210_0002
Create Date: 2025-12-15 08:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251215_0003"
down_revision = "20251210_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop existing foreign key constraint (name may vary; use convention if set). Try common name first.
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    fks = inspector.get_foreign_keys('admin_actions')
    for fk in fks:
        if fk['referred_table'] == 'users' and 'user_id' in fk['constrained_columns']:
            op.drop_constraint(fk['name'], 'admin_actions', type_='foreignkey')
            break
    # Alter column to be nullable
    op.alter_column('admin_actions', 'user_id', existing_type=sa.Integer(), nullable=True)
    # Recreate FK with ON DELETE SET NULL
    op.create_foreign_key(
        'fk_admin_actions_user_id_users',
        source_table='admin_actions',
        referent_table='users',
        local_cols=['user_id'],
        remote_cols=['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Drop the SET NULL FK
    op.drop_constraint('fk_admin_actions_user_id_users', 'admin_actions', type_='foreignkey')
    # Make column non-nullable again (may fail if nulls exist)
    op.alter_column('admin_actions', 'user_id', existing_type=sa.Integer(), nullable=False)
    # Recreate FK without ON DELETE (default NO ACTION)
    op.create_foreign_key(
        'fk_admin_actions_user_id_users',
        source_table='admin_actions',
        referent_table='users',
        local_cols=['user_id'],
        remote_cols=['id']
    )
