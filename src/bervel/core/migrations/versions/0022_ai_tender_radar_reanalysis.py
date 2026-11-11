"""Record the source ID for a manual AI tender reanalysis."""
from alembic import op
from sqlalchemy import Column, String


revision = '0022_ai_tender_radar_reanalysis'
down_revision = '0021_ai_tender_radar_analysis'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_tender_radar_jobs', Column('target_kontur_id', String(128)))


def downgrade():
    raise RuntimeError('Pending AI tender reanalysis targets must be retained')
