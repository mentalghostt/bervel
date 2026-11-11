"""Persistent fixed radar profiles, independent of inspection follow-ups."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0027b_radar_profiles'
down_revision = '0027a_radar_question_lane'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ai_radar_profiles',
        sa.Column('data_scope', sa.String(128), primary_key=True),
        sa.Column('source', sa.String(32), primary_key=True),
        sa.Column('run_id', UUID(as_uuid=True), sa.ForeignKey('ai_radar_runs.run_id'), unique=True),
        sa.Column('data', JSONB, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('test_api','kontur_api')", name='ai_radar_profile_source'))


def downgrade():
    op.drop_table('ai_radar_profiles')
