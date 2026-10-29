"""Atomic educational extraction and provenance."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0002_core_extraction'
down_revision = '0001_core_storage'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('datasets',
        sa.Column('dataset_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('artifact_id', UUID(as_uuid=True), sa.ForeignKey('artifacts.artifact_id'), nullable=False),
        sa.Column('data_scope', sa.String(128), nullable=False), sa.Column('owner_module_id', sa.String(128), nullable=False),
        sa.Column('current_revision', sa.Integer, nullable=False), sa.CheckConstraint('current_revision > 0'))
    op.create_table('input_snapshots',
        sa.Column('input_snapshot_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('data_scope', sa.String(128), nullable=False), sa.Column('inputs', JSONB, nullable=False))
    op.create_table('process_runs',
        sa.Column('process_run_id', UUID(as_uuid=True), primary_key=True), sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('input_snapshot_id', UUID(as_uuid=True), sa.ForeignKey('input_snapshots.input_snapshot_id'), nullable=False),
        sa.Column('producer', JSONB, nullable=False), sa.Column('configuration_revision', sa.Integer, nullable=False),
        sa.Column('state', sa.String(32), nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('dataset_revisions',
        sa.Column('dataset_id', UUID(as_uuid=True), sa.ForeignKey('datasets.dataset_id'), primary_key=True),
        sa.Column('revision', sa.Integer, primary_key=True), sa.Column('artifact_id', UUID(as_uuid=True), nullable=False),
        sa.Column('artifact_revision', sa.Integer, nullable=False),
        sa.Column('input_snapshot_id', UUID(as_uuid=True), sa.ForeignKey('input_snapshots.input_snapshot_id'), nullable=False),
        sa.Column('process_run_id', UUID(as_uuid=True), sa.ForeignKey('process_runs.process_run_id'), nullable=False),
        sa.Column('schema_id', sa.String(128), nullable=False), sa.Column('schema_version', sa.String(32), nullable=False),
        sa.Column('envelope', JSONB, nullable=False), sa.Column('record_count', sa.Integer, nullable=False),
        sa.ForeignKeyConstraint(['artifact_id', 'artifact_revision'], ['artifact_revisions.artifact_id', 'artifact_revisions.revision']),
        sa.ForeignKeyConstraint(['schema_id', 'schema_version'], ['schema_definitions.schema_id', 'schema_definitions.schema_version']),
        sa.CheckConstraint('revision > 0'), sa.CheckConstraint('record_count > 0'))
    op.create_table('data_records',
        sa.Column('dataset_id', UUID(as_uuid=True), primary_key=True), sa.Column('revision', sa.Integer, primary_key=True),
        sa.Column('ordinal', sa.Integer, primary_key=True), sa.Column('record_id', sa.String(128), nullable=False),
        sa.Column('record', JSONB, nullable=False),
        sa.ForeignKeyConstraint(['dataset_id', 'revision'], ['dataset_revisions.dataset_id', 'dataset_revisions.revision']),
        sa.UniqueConstraint('dataset_id', 'revision', 'record_id'))


def downgrade():
    for table in ['data_records', 'dataset_revisions', 'process_runs', 'input_snapshots', 'datasets']:
        op.drop_table(table)
