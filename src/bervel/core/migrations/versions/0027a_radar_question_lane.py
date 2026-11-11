"""Allow saved-card questions alongside the analysis queue in the same scope."""
from alembic import op
import sqlalchemy as sa

revision = '0027a_radar_question_lane'
down_revision = '0027_radar_legacy_history'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index('ai_tender_radar_one_active_job', table_name='ai_tender_radar_jobs')
    op.create_index('ai_tender_radar_one_active_job', 'ai_tender_radar_jobs',
        ['data_scope', sa.text("(CASE WHEN operation->>'kind' = 'question' THEN 'question' ELSE 'analysis' END)")],
        unique=True, postgresql_where=sa.text("state IN ('queued','running')"))


def downgrade():
    # A rollback is only valid while both lanes are idle. Never discard jobs.
    op.execute("LOCK TABLE ai_tender_radar_jobs IN ACCESS EXCLUSIVE MODE")
    active = op.get_bind().execute(sa.text("SELECT 1 FROM ai_tender_radar_jobs WHERE state IN ('queued','running') LIMIT 1")).first()
    if active is not None:
        raise RuntimeError('Wait for AI radar analysis and questions before rollback')
    op.drop_index('ai_tender_radar_one_active_job', table_name='ai_tender_radar_jobs')
    op.create_index('ai_tender_radar_one_active_job', 'ai_tender_radar_jobs', ['data_scope'],
        unique=True, postgresql_where=sa.text("state IN ('queued','running')"))
