"""Durable process commands, engine attempts and publication fencing."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0003_core_processes'
down_revision = '0002_core_extraction'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('process_jobs',
        sa.Column('process_run_id', UUID(as_uuid=True), sa.ForeignKey('process_runs.process_run_id'), primary_key=True),
        sa.Column('actor_id', sa.String(128), nullable=False), sa.Column('actor_permissions', JSONB, nullable=False),
        sa.Column('command', JSONB, nullable=False), sa.Column('workflow', JSONB, nullable=False),
        sa.Column('invocation_hash', sa.String(64), nullable=False), sa.Column('active_attempt', sa.Integer, nullable=False),
        sa.Column('result', JSONB), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('active_attempt > 0'))
    op.create_table('process_attempts',
        sa.Column('process_run_id', UUID(as_uuid=True), sa.ForeignKey('process_jobs.process_run_id'), primary_key=True),
        sa.Column('number', sa.Integer, primary_key=True), sa.Column('execution_id', sa.String(128), unique=True),
        sa.Column('parent_execution_id', sa.String(128)), sa.Column('state', sa.String(32), nullable=False),
        sa.Column('error_code', sa.String(64)), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False), sa.CheckConstraint('number > 0'))


def downgrade():
    op.drop_table('process_attempts')
    op.drop_table('process_jobs')
