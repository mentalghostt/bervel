"""Add immutable tender batches and durable radar runs (historical DDL)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0007_tender_radar'
down_revision = '0006_document_indexes'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('radar_batches',
        sa.Column('batch_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('actor_id', sa.String(128), nullable=False),
        sa.Column('artifact_id', UUID(as_uuid=True), nullable=False),
        sa.Column('artifact_revision', sa.Integer, nullable=False),
        sa.Column('filename', sa.Text, nullable=False),
        sa.Column('data', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['artifact_id', 'artifact_revision'], ['artifact_revisions.artifact_id', 'artifact_revisions.revision']))
    op.create_table('radar_runs',
        sa.Column('run_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('batch_id', UUID(as_uuid=True), sa.ForeignKey('radar_batches.batch_id'), nullable=False),
        sa.Column('actor_id', sa.String(128), nullable=False),
        sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('actor_permissions', JSONB, nullable=False),
        sa.Column('sources_snapshot', JSONB, nullable=False),
        sa.Column('focus', sa.Text, nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('report', JSONB), sa.Column('model', sa.Text), sa.Column('error', sa.Text),
        sa.CheckConstraint("state IN ('queued','running','completed','failed')"))
    op.create_index('ix_radar_runs_queue', 'radar_runs', ['state', 'created_at'])


def downgrade():
    op.drop_table('radar_runs')
    op.drop_table('radar_batches')
