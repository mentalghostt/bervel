"""Persist verified AI tender analysis alongside the source snapshot."""
from alembic import op
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB


revision = '0021_ai_tender_radar_analysis'
down_revision = '0020_ai_tender_radar_jobs'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_tender_radar_jobs', Column('analysis', JSONB(none_as_null=True)))


def downgrade():
    raise RuntimeError('AI tender analysis results must be retained')
