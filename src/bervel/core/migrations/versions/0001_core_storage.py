"""Initial educational core storage."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0001_core_storage'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('artifacts',
        sa.Column('artifact_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('owner_module_id', sa.String(128), nullable=False),
        sa.Column('current_revision', sa.Integer, nullable=False), sa.CheckConstraint('current_revision > 0'))
    op.create_table('artifact_revisions',
        sa.Column('artifact_id', UUID(as_uuid=True), sa.ForeignKey('artifacts.artifact_id'), primary_key=True),
        sa.Column('revision', sa.Integer, primary_key=True), sa.Column('metadata', JSONB, nullable=False),
        sa.CheckConstraint('revision > 0'))
    op.create_table('schema_definitions',
        sa.Column('schema_id', sa.String(128), primary_key=True), sa.Column('schema_version', sa.String(32), primary_key=True),
        sa.Column('checksum', sa.String(64), nullable=False), sa.Column('definition', JSONB, nullable=False))
    op.create_table('command_receipts',
        sa.Column('data_scope', sa.String(128), primary_key=True), sa.Column('actor_id', sa.String(128), primary_key=True),
        sa.Column('operation', sa.String(128), primary_key=True), sa.Column('command_key', sa.String(128), primary_key=True),
        sa.Column('fingerprint', sa.String(64), nullable=False), sa.Column('result', JSONB, nullable=False))
    op.create_table('audit',
        sa.Column('audit_id', UUID(as_uuid=True), primary_key=True), sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('actor_id', sa.String(128), nullable=False), sa.Column('operation', sa.String(128), nullable=False),
        sa.Column('object_ref', JSONB, nullable=False), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for table in ['audit', 'command_receipts', 'schema_definitions', 'artifact_revisions', 'artifacts']:
        op.drop_table(table)
