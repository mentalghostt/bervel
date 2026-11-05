"""Add the independent AI tender intake queue and its source snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = '0020_ai_tender_radar_jobs'
down_revision = '0019_radar_retry'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_tender_radar_jobs',
        sa.Column('job_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('actor_id', sa.String(128), nullable=False),
        sa.Column('command_key', sa.String(128), nullable=False),
        sa.Column('stock_artifact_id', UUID(as_uuid=True), nullable=False),
        sa.Column('stock_sha256', sa.String(64), nullable=False),
        sa.Column('search_phrase', sa.String(500), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('kontur_id', sa.String(128)),
        sa.Column('stock', JSONB), sa.Column('tender', JSONB), sa.Column('snapshot', JSONB), sa.Column('error', JSONB),
        sa.UniqueConstraint('data_scope', 'actor_id', 'command_key', name='ai_tender_radar_job_command'),
        sa.CheckConstraint("state IN ('queued','running','loaded','no_match','failed')", name='ai_tender_radar_job_state'),
    )
    op.create_index('ai_tender_radar_one_active_job', 'ai_tender_radar_jobs', ['data_scope'], unique=True,
                    postgresql_where=sa.text("state IN ('queued','running')"))
    op.create_index('ai_tender_radar_job_queue', 'ai_tender_radar_jobs', ['created_at', 'job_id'],
                    postgresql_where=sa.text("state = 'queued'"))
    op.create_index('ai_tender_radar_loaded_ids', 'ai_tender_radar_jobs', ['data_scope', 'kontur_id'],
                    postgresql_where=sa.text("state = 'loaded'"))


def downgrade():
    raise RuntimeError('AI tender intake jobs and source snapshots must be retained')
