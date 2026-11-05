"""A queued API acquisition has no batch until its immutable snapshot is saved."""
from alembic import op
import sqlalchemy as sa

revision = '0016_radar_api_source'
down_revision = '0015_inspection_sessions'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('radar_runs', 'batch_id', existing_type=sa.UUID(), nullable=True)


def downgrade():
    raise RuntimeError('API acquisition history must be preserved')
