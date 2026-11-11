"""Pin existing visible AI radar results without changing their source records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision='0027_radar_legacy_history'
down_revision='0026_ai_radar_runs'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('ai_radar_legacy_groups',
        sa.Column('data_scope',sa.String(128),primary_key=True),
        sa.Column('job_ids',JSONB,nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    op.execute(sa.text("""
        INSERT INTO ai_radar_legacy_groups(data_scope,job_ids)
        SELECT data_scope,jsonb_agg(job_id::text ORDER BY updated_at DESC,job_id DESC)
        FROM (
            SELECT DISTINCT ON (data_scope,kontur_id) data_scope,job_id,updated_at
            FROM ai_tender_radar_jobs
            WHERE state='loaded' AND analysis IS NOT NULL
            ORDER BY data_scope,kontur_id,
                     CASE WHEN analysis->>'status'='partial' THEN 1 ELSE 0 END,
                     updated_at DESC,job_id DESC
        ) visible GROUP BY data_scope
    """))


def downgrade():
    op.drop_table('ai_radar_legacy_groups')
