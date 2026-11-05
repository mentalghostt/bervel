"""Durable immutable engineering proposals; only state/decision receipt may change."""
from sqlalchemy import Table, Column, String, Integer, ForeignKey, UniqueConstraint, DateTime, CheckConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from bervel.core.infrastructure.database import metadata

engineering_proposals = Table('inspection_proposals', metadata,
    Column('proposal_id', UUID(as_uuid=True), primary_key=True),
    Column('session_id', UUID(as_uuid=True), ForeignKey('chat_sessions.session_id', ondelete='CASCADE'), nullable=False),
    Column('turn_id', UUID(as_uuid=True), nullable=False), Column('ordinal', Integer, nullable=False),
    Column('actor_id', String(128), nullable=False), Column('data_scope', String(128), nullable=False),
    Column('fingerprint', String(64), nullable=False), Column('payload', JSONB, nullable=False),
    Column('card', JSONB, nullable=False), Column('status', String(16), nullable=False),
    Column('command_key', String(128)), Column('decision', String(16)),
    Column('created_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('turn_id', 'ordinal'),
    UniqueConstraint('actor_id', 'data_scope', 'command_key'),
    CheckConstraint("status IN ('pending','applying','applied','rejected','failed','unknown')"))
