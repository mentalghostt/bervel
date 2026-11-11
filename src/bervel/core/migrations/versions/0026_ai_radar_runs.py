"""Profile-driven operations use the existing AI job queue."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0026_ai_radar_runs'
down_revision = '0025_company_settings'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_tender_radar_jobs', sa.Column('operation', JSONB, nullable=True))
    op.create_table('ai_radar_runs',
        sa.Column('run_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('settings_snapshot_id', UUID(as_uuid=True), nullable=False),
        sa.Column('data', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_ai_radar_runs_scope', 'ai_radar_runs', ['data_scope', 'created_at'])
    op.create_table('ai_radar_analyses',
        sa.Column('analysis_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('run_id', UUID(as_uuid=True), sa.ForeignKey('ai_radar_runs.run_id'), nullable=False),
        sa.Column('candidate_id', sa.String(128), nullable=False),
        sa.Column('data', JSONB, nullable=False),
        sa.UniqueConstraint('run_id', 'candidate_id', name='uq_ai_radar_run_candidate'))
    op.create_table('ai_radar_reports',
        sa.Column('job_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('run_id', UUID(as_uuid=True), sa.ForeignKey('ai_radar_runs.run_id'), nullable=False),
        sa.Column('data', JSONB, nullable=False))
    op.create_table('ai_radar_search_pages',
        sa.Column('page_id', sa.String(64), primary_key=True),
        sa.Column('run_id', UUID(as_uuid=True), sa.ForeignKey('ai_radar_runs.run_id'), nullable=False),
        sa.Column('data', JSONB, nullable=False))


def downgrade():
    op.drop_table('ai_radar_search_pages')
    op.drop_table('ai_radar_reports')
    op.drop_table('ai_radar_analyses')
    op.drop_table('ai_radar_runs')
    op.drop_column('ai_tender_radar_jobs', 'operation')
