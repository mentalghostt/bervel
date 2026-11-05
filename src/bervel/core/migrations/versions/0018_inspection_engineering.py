"""Immutable confirmed inspection patches."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
revision = '0018_inspection_engineering'
down_revision = '0017_inspection_continuation'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('inspection_proposals',
        sa.Column('proposal_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('session_id', UUID(as_uuid=True), sa.ForeignKey('chat_sessions.session_id', ondelete='CASCADE'), nullable=False),
        sa.Column('turn_id', UUID(as_uuid=True), nullable=False), sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.String(128), nullable=False), sa.Column('data_scope', sa.String(128), nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False), sa.Column('payload', JSONB(), nullable=False),
        sa.Column('card', JSONB(), nullable=False), sa.Column('status', sa.String(16), nullable=False),
        sa.Column('command_key', sa.String(128)), sa.Column('decision', sa.String(16)),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('turn_id', 'ordinal'),
        sa.UniqueConstraint('actor_id', 'data_scope', 'command_key'),
        sa.CheckConstraint("status IN ('pending','applying','applied','rejected','failed','unknown')"))


def downgrade():
    raise RuntimeError('Confirmed patch receipts must be retained')
