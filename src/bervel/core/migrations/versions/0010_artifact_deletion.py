"""Delete original revisions while retaining independent radar history and retryable cleanup."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = '0010_artifact_deletion'
down_revision = '0009_radar_stock_selection'
branch_labels = None
depends_on = None


def upgrade():
    # Radar imports are self-contained historical snapshots, not owned caches of an original.
    # Discover the constraint by target: PostgreSQL-generated names also occur in old installs.
    for constraint in sa.inspect(op.get_bind()).get_foreign_keys('radar_batches'):
        if constraint['referred_table'] == 'artifact_revisions':
            op.drop_constraint(constraint['name'], 'radar_batches', type_='foreignkey')
    op.create_table('artifact_object_deletions',
        sa.Column('artifact_id', UUID(as_uuid=True), sa.ForeignKey('artifacts.artifact_id'), primary_key=True),
        sa.Column('object_ref', sa.String(71), primary_key=True))


def downgrade():
    # A historical source may already have been deleted; restoring the FK would discard history.
    raise RuntimeError('Original deletion cannot be reversed by a schema downgrade')
