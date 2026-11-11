"""Durable user cancellation of inspection turns."""
from alembic import op
import sqlalchemy as sa

revision = '0017_inspection_continuation'
down_revision = '0016_radar_api_source'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chat_turns', sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('chat_turns', 'cancel_requested')
