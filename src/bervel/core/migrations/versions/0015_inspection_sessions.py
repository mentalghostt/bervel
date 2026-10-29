"""Separate inspection history without rewriting existing conversations."""
from alembic import op
import sqlalchemy as sa

revision = '0015_inspection_sessions'
down_revision = '0014_chat_workflow'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_sessions', sa.Column('mode', sa.String(16), nullable=False, server_default='assistant'))
    op.create_check_constraint('chat_sessions_mode_check', 'chat_sessions', "mode IN ('assistant', 'inspection')")
    op.create_index('ix_chat_sessions_mode', 'chat_sessions', ['mode', 'actor_id', 'data_scope', 'created_at'])


def downgrade():
    raise RuntimeError('Inspection conversations must not become writable assistant conversations')
