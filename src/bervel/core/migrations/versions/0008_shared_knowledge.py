"""Versioned evidence chunks and embedding work; pgvector installed by DB admin."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision='0008_shared_knowledge'
down_revision='0007_tender_radar'
branch_labels=None
depends_on=None


class Vector(sa.types.UserDefinedType):
    def get_col_spec(self, **kw): return 'public.vector'


def upgrade():
    op.add_column('artifacts',sa.Column('deleted_at',sa.DateTime(timezone=True)))
    op.create_table('knowledge_jobs',
        sa.Column('artifact_id',UUID(as_uuid=True),primary_key=True),
        sa.Column('revision',sa.Integer,primary_key=True),
        sa.Column('state',sa.String(16),nullable=False,server_default='pending'),
        sa.Column('error',sa.Text),
        sa.Column('total_chunks',sa.Integer,nullable=False,server_default='0'),
        sa.Column('completed_chunks',sa.Integer,nullable=False,server_default='0'),
        sa.Column('source_status',sa.String(16),nullable=False,server_default='ready'),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['artifact_id','revision'],['artifact_revisions.artifact_id','artifact_revisions.revision']),
        sa.CheckConstraint("state IN ('pending','running','ready','partial','unsupported','failed')"))
    op.create_index('ix_knowledge_jobs_pending','knowledge_jobs',['state','updated_at'])
    op.create_table('knowledge_chunks',
        sa.Column('artifact_id',UUID(as_uuid=True),primary_key=True),
        sa.Column('revision',sa.Integer,primary_key=True),
        sa.Column('ordinal',sa.Integer,primary_key=True),
        sa.Column('version',sa.Integer,nullable=False),
        sa.Column('source',JSONB,nullable=False),
        sa.Column('embedding_input',sa.Text,nullable=False),
        sa.Column('content_hash',sa.String(64),nullable=False),
        sa.Column('tokens',sa.Integer,nullable=False),
        sa.Column('model',sa.String(100),nullable=False),
        sa.Column('embedding',Vector()),
        sa.ForeignKeyConstraint(['artifact_id','revision'],['artifact_revisions.artifact_id','artifact_revisions.revision']))
    op.create_index('ix_knowledge_chunks_content','knowledge_chunks',['model','content_hash'])


def downgrade():
    op.drop_table('knowledge_chunks')
    op.drop_table('knowledge_jobs')
    op.drop_column('artifacts','deleted_at')
