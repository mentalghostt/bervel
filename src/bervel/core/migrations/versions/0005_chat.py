"""Persist chat sessions, immutable source snapshots and raw CSV fragments."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0005_chat'
down_revision = '0004_python_processes'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('chat_sessions',
        sa.Column('session_id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('actor_id', sa.String(128), nullable=False),
        sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_table('chat_turns',
        sa.Column('turn_id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', pg.UUID(as_uuid=True), sa.ForeignKey('chat_sessions.session_id'), nullable=False),
        sa.Column('ordinal', sa.Integer, nullable=False), sa.Column('question', sa.Text, nullable=False),
        sa.Column('answer', sa.Text), sa.Column('model', sa.String(200)), sa.Column('error', sa.Text),
        sa.Column('state', sa.String(16), nullable=False), sa.Column('snapshot', pg.JSONB),
        sa.Column('sources', pg.JSONB, nullable=False), sa.Column('actor_permissions', pg.JSONB, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('session_id', 'ordinal'), sa.CheckConstraint('ordinal > 0'),
        sa.CheckConstraint("state IN ('queued', 'running', 'completed', 'failed')"))
    op.create_index('ix_chat_turns_queue', 'chat_turns', ['state', 'created_at'])
    op.create_table('csv_chunks',
        sa.Column('artifact_id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('revision', sa.Integer, primary_key=True), sa.Column('ordinal', sa.Integer, primary_key=True),
        sa.Column('row_start', sa.Integer, nullable=False), sa.Column('row_end', sa.Integer, nullable=False),
        sa.Column('total_rows', sa.Integer, nullable=False), sa.Column('excerpt', sa.Text, nullable=False),
        sa.ForeignKeyConstraint(['artifact_id', 'revision'], ['artifact_revisions.artifact_id', 'artifact_revisions.revision']))


def downgrade():
    op.drop_table('csv_chunks')
    op.drop_index('ix_chat_turns_queue', table_name='chat_turns')
    op.drop_table('chat_turns')
    op.drop_table('chat_sessions')
