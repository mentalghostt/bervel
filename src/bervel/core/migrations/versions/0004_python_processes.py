"""Replace external callback credentials with direct Python queue ownership."""
from alembic import op
import sqlalchemy as sa

revision = '0004_python_processes'
down_revision = '0003_core_processes'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column('process_jobs', 'invocation_hash')
    op.create_index('ix_process_runs_queue', 'process_runs', ['state', 'created_at'])


def downgrade():
    op.drop_index('ix_process_runs_queue', table_name='process_runs')
    op.add_column('process_jobs', sa.Column('invocation_hash', sa.String(64), nullable=False, server_default=''))
