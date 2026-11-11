"""Schedule definite temporary model refusals in the existing radar queue."""
from alembic import op
import sqlalchemy as sa

revision='0019_radar_retry'
down_revision='0018_inspection_engineering'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('radar_runs',sa.Column('retry_at',sa.DateTime(timezone=True),nullable=True))


def downgrade():
    raise RuntimeError('Radar progress and queued retry times must be retained')
