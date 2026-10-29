"""Persist long task progress without fabricating phases for historical turns."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0014_chat_workflow'
down_revision = '0013_files_without_revisions'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_turns', sa.Column('workflow', JSONB(none_as_null=True), nullable=True))
    op.drop_constraint('chat_turns_state_check', 'chat_turns', type_='check')
    op.create_check_constraint('chat_turns_state_check', 'chat_turns',
        "state IN ('queued', 'running', 'completed', 'partial', 'blocked', 'failed')")


def downgrade():
    raise RuntimeError('Durable chat progress and terminal outcomes must not be discarded by downgrade')
