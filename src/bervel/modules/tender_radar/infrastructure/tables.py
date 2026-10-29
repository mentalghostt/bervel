"""Tables owned exclusively by tender radar."""
from sqlalchemy import Table, Column, String, Integer, Text, ForeignKey, CheckConstraint, UniqueConstraint, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from bervel.core.infrastructure.database import metadata

radar_batches = Table('radar_batches', metadata,
    Column('batch_id', UUID(as_uuid=True), primary_key=True), Column('data_scope', String(128), nullable=False),
    Column('actor_id', String(128), nullable=False), Column('artifact_id', UUID(as_uuid=True), nullable=False),
    Column('filename', Text, nullable=False),
    Column('data', JSONB, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False))
radar_runs = Table('radar_runs', metadata,
    Column('run_id', UUID(as_uuid=True), primary_key=True), Column('batch_id', UUID(as_uuid=True), ForeignKey('radar_batches.batch_id'), nullable=False),
    Column('actor_id', String(128), nullable=False), Column('data_scope', String(128), nullable=False),
    Column('actor_permissions', JSONB, nullable=False), Column('sources_snapshot', JSONB, nullable=False),
    Column('focus', Text, nullable=False), Column('state', String(16), nullable=False),
    Column('selection',JSONB,nullable=True),
    Column('created_at', DateTime(timezone=True), nullable=False), Column('report', JSONB), Column('model', Text), Column('error', Text),
    CheckConstraint("state IN ('queued','running','completed','failed')"))
Index('ix_radar_runs_queue',radar_runs.c.state,radar_runs.c.created_at)


