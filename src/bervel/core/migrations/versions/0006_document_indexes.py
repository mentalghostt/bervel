"""Cache versioned text/OCR results for format-independent document retrieval."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = '0006_document_indexes'
down_revision = '0005_chat'
branch_labels = depends_on = None


def upgrade():
    op.create_table('document_indexes', sa.Column('artifact_id', pg.UUID(as_uuid=True), primary_key=True),
        sa.Column('revision', sa.Integer, primary_key=True), sa.Column('version', sa.Integer, nullable=False),
        sa.Column('result', pg.JSONB, nullable=False),
        sa.ForeignKeyConstraint(['artifact_id', 'revision'], ['artifact_revisions.artifact_id', 'artifact_revisions.revision']))


def downgrade():
    op.drop_table('document_indexes')
