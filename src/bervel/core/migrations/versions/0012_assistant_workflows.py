"""Durable assistant effects and workspace ticket drafts; existing conversation text is unchanged."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0012_assistant_workflows'
down_revision = '0011_artifact_usage'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_turns', sa.Column('results', JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.create_table('chat_actions',
        sa.Column('action_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('turn_id', UUID(as_uuid=True), nullable=False),
        sa.Column('ordinal', sa.Integer, nullable=False),
        sa.Column('actor_id', sa.String(128), nullable=False), sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('operation', sa.String(64), nullable=False), sa.Column('arguments', JSONB, nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False), sa.Column('state', sa.String(16), nullable=False),
        sa.Column('result', JSONB), sa.Column('error', JSONB),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('data_scope', 'actor_id', 'turn_id', 'ordinal'), sa.CheckConstraint('ordinal >= 0'),
        sa.CheckConstraint("state IN ('running','completed','failed','unknown')"))
    op.create_table('tickets',
        sa.Column('ticket_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('actor_id', sa.String(128), nullable=False), sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('content', JSONB, nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('revision > 0'), sa.CheckConstraint("status IN ('created','sent')"))
    op.create_index('ix_tickets_scope_created', 'tickets', ['data_scope', 'created_at'])


def downgrade():
    raise RuntimeError('Assistant history and ticket drafts must not be discarded by downgrade')
