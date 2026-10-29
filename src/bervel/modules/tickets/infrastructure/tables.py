"""Tables owned by the tickets module."""
from sqlalchemy import Table, Column, String, Integer, CheckConstraint, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from bervel.core.infrastructure.database import metadata

tickets = Table('tickets', metadata,
    Column('ticket_id', UUID(as_uuid=True), primary_key=True),
    Column('actor_id', String(128), nullable=False), Column('data_scope', String(128), nullable=False),
    Column('content', JSONB, nullable=False), Column('status', String(16), nullable=False),
    Column('revision', Integer, nullable=False), Column('created_at', DateTime(timezone=True), nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False),
    CheckConstraint('revision > 0'), CheckConstraint("status IN ('created','sent')"))
Index('ix_tickets_scope_created', tickets.c.data_scope, tickets.c.created_at)
