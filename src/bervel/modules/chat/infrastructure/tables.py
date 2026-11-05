"""Tables owned exclusively by chat; metadata is shared with the platform."""
from sqlalchemy import Table, Column, String, Integer, Text, ForeignKey, ForeignKeyConstraint, CheckConstraint, UniqueConstraint, DateTime, Index, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID
from bervel.core.infrastructure.database import metadata

chat_sessions = Table('chat_sessions', metadata,
    Column('session_id', UUID(as_uuid=True), primary_key=True),
    Column('mode', String(16), nullable=False, server_default='assistant'),
    Column('actor_id', String(128), nullable=False), Column('data_scope', String(128), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False),
    CheckConstraint("mode IN ('assistant', 'inspection')", name='chat_sessions_mode_check'))
chat_turns = Table('chat_turns', metadata,
    Column('turn_id', UUID(as_uuid=True), primary_key=True),
    Column('session_id', UUID(as_uuid=True), ForeignKey('chat_sessions.session_id'), nullable=False),
    Column('ordinal', Integer, nullable=False), Column('question', Text, nullable=False),
    Column('answer', Text), Column('model', String(200)), Column('error', Text),
    Column('state', String(16), nullable=False), Column('snapshot', JSONB),
    Column('sources', JSONB, nullable=False), Column('actor_permissions', JSONB, nullable=False),
    Column('results', JSONB, nullable=False, server_default='[]'),
    Column('workflow', JSONB(none_as_null=True)),
    Column('cancel_requested', Boolean, nullable=False, server_default='false'),
    Column('created_at', DateTime(timezone=True), nullable=False),
    UniqueConstraint('session_id', 'ordinal'), CheckConstraint('ordinal > 0'),
    CheckConstraint("state IN ('queued', 'running', 'completed', 'partial', 'blocked', 'failed')", name='chat_turns_state_check'))
Index('ix_chat_turns_queue', chat_turns.c.state, chat_turns.c.created_at)

chat_actions = Table('chat_actions', metadata,
    Column('action_id', UUID(as_uuid=True), primary_key=True), Column('turn_id', UUID(as_uuid=True), nullable=False),
    Column('ordinal', Integer, nullable=False), Column('actor_id', String(128), nullable=False),
    Column('data_scope', String(128), nullable=False), Column('operation', String(64), nullable=False),
    Column('arguments', JSONB, nullable=False), Column('fingerprint', String(64), nullable=False),
    Column('state', String(16), nullable=False), Column('result', JSONB), Column('error', JSONB),
    Column('created_at', DateTime(timezone=True), nullable=False), Column('finished_at', DateTime(timezone=True)),
    UniqueConstraint('data_scope', 'actor_id', 'turn_id', 'ordinal'), CheckConstraint('ordinal >= 0'),
    CheckConstraint("state IN ('running','completed','failed','unknown')"))
