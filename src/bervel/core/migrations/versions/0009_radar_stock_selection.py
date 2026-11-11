"""Pin radar stock source and quantity filters without rewriting historical runs."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='0009_radar_stock_selection'
down_revision='0008_shared_knowledge'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('radar_runs',sa.Column('selection',JSONB,nullable=True))


def downgrade():
    op.drop_column('radar_runs','selection')
