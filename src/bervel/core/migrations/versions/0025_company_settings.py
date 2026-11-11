"""Immutable settings snapshots with experimental products stored independently."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0025_company_settings'
down_revision = '0024_retire_legacy_radar'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('company_settings_snapshots',
        sa.Column('id',postgresql.UUID(as_uuid=True),primary_key=True),
        sa.Column('data_scope',sa.String(128),nullable=False),
        sa.Column('actor_id',sa.String(128),nullable=False),
        sa.Column('command_key',sa.String(128),nullable=False),
        sa.Column('fingerprint',sa.String(64),nullable=False),
        sa.Column('profile',postgresql.JSONB(),nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.UniqueConstraint('data_scope','actor_id','command_key',name='uq_company_settings_command'))
    op.create_index('ix_company_settings_scope','company_settings_snapshots',['data_scope'])
    op.create_table('company_settings_heads',
        sa.Column('data_scope',sa.String(128),primary_key=True),
        sa.Column('snapshot_id',postgresql.UUID(as_uuid=True),sa.ForeignKey('company_settings_snapshots.id'),nullable=False))
    op.create_table('company_settings_experimental_products',
        sa.Column('snapshot_id',postgresql.UUID(as_uuid=True),sa.ForeignKey('company_settings_snapshots.id'),primary_key=True),
        sa.Column('ordinal',sa.Integer(),primary_key=True),
        sa.Column('product',postgresql.JSONB(),nullable=False))


def downgrade():
    op.drop_table('company_settings_experimental_products')
    op.drop_table('company_settings_heads')
    op.drop_table('company_settings_snapshots')
