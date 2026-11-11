"""Presales owns its snapshots, request history, lines and work queue."""
from sqlalchemy import (Table, Column, String, Integer, DateTime, ForeignKey, UniqueConstraint,
                        CheckConstraint, Index, Boolean)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from bervel.core.infrastructure.database import metadata


settings_snapshots = Table('presales_settings_snapshots', metadata,
    Column('snapshot_id', UUID(as_uuid=True), primary_key=True),
    Column('data_scope', String(128), nullable=False),
    Column('version_no', Integer, nullable=False), Column('settings', JSONB, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('data_scope', 'version_no', name='presales_settings_version'))

requests = Table('presales_requests', metadata,
    Column('request_id', UUID(as_uuid=True), primary_key=True),
    Column('data_scope', String(128), nullable=False), Column('title', String, nullable=False),
    Column('client', String), Column('current_revision_id', UUID(as_uuid=True), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False))
Index('ix_presales_requests_scope', requests.c.data_scope, requests.c.created_at, requests.c.request_id)

revisions = Table('presales_revisions', metadata,
    Column('revision_id', UUID(as_uuid=True), primary_key=True),
    Column('request_id', UUID(as_uuid=True), ForeignKey('presales_requests.request_id'), nullable=False),
    Column('data_scope', String(128), nullable=False), Column('revision_number', Integer, nullable=False),
    Column('parent_revision_id', UUID(as_uuid=True)),
    Column('created_at', DateTime(timezone=True), nullable=False),
    Column('evaluated_at', DateTime(timezone=True), nullable=False),
    Column('state', String(24), nullable=False), Column('phase', String(32), nullable=False),
    Column('inputs', JSONB, nullable=False), Column('decisions', JSONB, nullable=False),
    Column('extracted', JSONB), Column('analysis', JSONB), Column('offer', JSONB),
    Column('artifacts', JSONB, nullable=False),
    UniqueConstraint('request_id', 'revision_number', name='presales_revision_number'))

lines = Table('presales_lines', metadata,
    Column('revision_id', UUID(as_uuid=True), ForeignKey('presales_revisions.revision_id'), primary_key=True),
    Column('line_id', String(128), primary_key=True), Column('ordinal', Integer, nullable=False),
    Column('record', JSONB, nullable=False))
Index('ix_presales_line_order', lines.c.revision_id, lines.c.ordinal)

jobs = Table('presales_jobs', metadata,
    Column('job_id', UUID(as_uuid=True), primary_key=True),
    Column('request_id', UUID(as_uuid=True), ForeignKey('presales_requests.request_id'), nullable=False),
    Column('revision_id', UUID(as_uuid=True), ForeignKey('presales_revisions.revision_id'), nullable=False, unique=True),
    Column('data_scope', String(128), nullable=False), Column('actor_id', String(128), nullable=False),
    Column('permissions', JSONB, nullable=False), Column('state', String(24), nullable=False),
    Column('phase', String(32), nullable=False), Column('stop_requested', Boolean, nullable=False),
    Column('lease_token', UUID(as_uuid=True)), Column('lease_until', DateTime(timezone=True)),
    Column('cache', JSONB, nullable=False), Column('progress', JSONB, nullable=False),
    Column('error', JSONB), Column('created_at', DateTime(timezone=True), nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False),
    CheckConstraint("state IN ('queued','running','stopped','failed','complete')", name='presales_job_state'))
Index('ix_presales_job_queue', jobs.c.state, jobs.c.created_at)
