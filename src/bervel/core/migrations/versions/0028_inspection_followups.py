"""Durable inbox for messages steering an active native inspection turn."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0028_inspection_followups'
down_revision = '0027a_radar_question_lane'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_turns', sa.Column('followup_of', UUID(as_uuid=True), nullable=True))
    op.add_column('chat_turns', sa.Column('delivery_state', sa.String(16), nullable=True))
    op.create_foreign_key('chat_turns_followup_fk', 'chat_turns', 'chat_turns', ['followup_of'], ['turn_id'], ondelete='CASCADE')
    op.create_check_constraint('chat_turns_delivery_check', 'chat_turns', "delivery_state IN ('pending','sending','accepted','unknown')")
    op.create_check_constraint('chat_turns_followup_check', 'chat_turns', '(followup_of IS NULL) = (delivery_state IS NULL)')
    op.create_index('ix_chat_turns_followups', 'chat_turns', ['followup_of', 'ordinal'])


def downgrade():
    op.drop_index('ix_chat_turns_followups', table_name='chat_turns')
    op.drop_constraint('chat_turns_followup_check', 'chat_turns', type_='check')
    op.drop_constraint('chat_turns_delivery_check', 'chat_turns', type_='check')
    op.drop_constraint('chat_turns_followup_fk', 'chat_turns', type_='foreignkey')
    op.drop_column('chat_turns', 'delivery_state')
    op.drop_column('chat_turns', 'followup_of')
