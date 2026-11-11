"""Typed file purpose independent from immutable original bytes and revisions."""
from alembic import op
import sqlalchemy as sa

revision='0011_artifact_usage'
down_revision='0010_artifact_deletion'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('artifacts',sa.Column('usage_role',sa.String(32),nullable=False,server_default='general'))
    op.create_check_constraint('artifact_usage_role','artifacts',"usage_role IN ('general','procurement','stock','economics')")


def downgrade():
    op.drop_constraint('artifact_usage_role','artifacts',type_='check')
    op.drop_column('artifacts','usage_role')
