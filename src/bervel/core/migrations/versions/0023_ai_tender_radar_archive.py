"""Queue all purchases from an immutable JSON archive."""
from alembic import op
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import UUID


revision = '0023_ai_tender_radar_archive'
down_revision = '0022_ai_tender_radar_reanalysis'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ai_tender_radar_jobs', Column('batch_id', UUID(as_uuid=True)))
    op.drop_constraint('ai_tender_radar_job_state', 'ai_tender_radar_jobs', type_='check')
    op.create_check_constraint('ai_tender_radar_job_state', 'ai_tender_radar_jobs',
        "state IN ('waiting','queued','running','loaded','no_match','failed')")
    op.create_index('ai_tender_radar_archive_batch', 'ai_tender_radar_jobs', ['batch_id', 'state', 'command_key'])


def downgrade():
    raise RuntimeError('AI tender archive jobs and source snapshots must be retained')
