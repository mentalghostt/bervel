"""Independent customer-request pipeline; does not require inspection followups."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0029_presales_pipeline'
down_revision = '0027b_radar_profiles'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('presales_settings_snapshots',
        sa.Column('snapshot_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('version_no', sa.Integer, nullable=False), sa.Column('settings', JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('data_scope', 'version_no', name='presales_settings_version'))
    op.create_table('presales_requests',
        sa.Column('request_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('data_scope', sa.String(128), nullable=False), sa.Column('title', sa.String, nullable=False),
        sa.Column('client', sa.String), sa.Column('current_revision_id', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_presales_requests_scope', 'presales_requests', ['data_scope', 'created_at', 'request_id'])
    op.create_table('presales_revisions',
        sa.Column('revision_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('request_id', UUID(as_uuid=True), sa.ForeignKey('presales_requests.request_id'), nullable=False),
        sa.Column('data_scope', sa.String(128), nullable=False), sa.Column('revision_number', sa.Integer, nullable=False),
        sa.Column('parent_revision_id', UUID(as_uuid=True)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(24), nullable=False), sa.Column('phase', sa.String(32), nullable=False),
        sa.Column('inputs', JSONB, nullable=False), sa.Column('decisions', JSONB, nullable=False),
        sa.Column('extracted', JSONB), sa.Column('analysis', JSONB), sa.Column('offer', JSONB),
        sa.Column('artifacts', JSONB, nullable=False),
        sa.UniqueConstraint('request_id', 'revision_number', name='presales_revision_number'))
    op.create_table('presales_lines',
        sa.Column('revision_id', UUID(as_uuid=True), sa.ForeignKey('presales_revisions.revision_id'), primary_key=True),
        sa.Column('line_id', sa.String(128), primary_key=True), sa.Column('ordinal', sa.Integer, nullable=False),
        sa.Column('record', JSONB, nullable=False))
    op.create_index('ix_presales_line_order', 'presales_lines', ['revision_id', 'ordinal'])
    op.create_table('presales_jobs',
        sa.Column('job_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('request_id', UUID(as_uuid=True), sa.ForeignKey('presales_requests.request_id'), nullable=False),
        sa.Column('revision_id', UUID(as_uuid=True), sa.ForeignKey('presales_revisions.revision_id'), nullable=False, unique=True),
        sa.Column('data_scope', sa.String(128), nullable=False), sa.Column('actor_id', sa.String(128), nullable=False),
        sa.Column('permissions', JSONB, nullable=False), sa.Column('state', sa.String(24), nullable=False),
        sa.Column('phase', sa.String(32), nullable=False), sa.Column('stop_requested', sa.Boolean, nullable=False),
        sa.Column('lease_token', UUID(as_uuid=True)), sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('cache', JSONB, nullable=False), sa.Column('progress', JSONB, nullable=False),
        sa.Column('error', JSONB), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('queued','running','stopped','failed','complete')", name='presales_job_state'))
    op.create_index('ix_presales_job_queue', 'presales_jobs', ['state', 'created_at'])


def downgrade():
    for name in ('presales_jobs', 'presales_lines', 'presales_revisions', 'presales_requests', 'presales_settings_snapshots'):
        op.drop_table(name)
