"""Ticket edits and their idempotency receipts commit atomically."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.core.infrastructure.commands import CommandReceipts, digest
from bervel.modules.tickets.infrastructure.tables import tickets


def view(row) -> dict:
    return {**row['content'], 'ticket_id': str(row['ticket_id']), 'status': row['status'], 'revision': row['revision'],
        'created_at': row['created_at'].isoformat(), 'updated_at': row['updated_at'].isoformat()}


class PostgresTicketRepository:
    def __init__(self, engine: Engine):
        self.engine, self.receipts = engine, CommandReceipts()

    def _read(self, connection, actor, ticket_id, *, locked=False):
        statement = select(tickets).where(tickets.c.ticket_id == ticket_id, tickets.c.data_scope == actor.data_scope)
        row = connection.execute(statement.with_for_update() if locked else statement).mappings().first()
        if row is None:
            raise CoreError('NOT_FOUND', 'Обращение не найдено.')
        return row

    def create(self, actor: ActorContext, content: dict, key: str) -> dict:
        fingerprint = digest(content)
        with self.engine.begin() as connection:
            repeat = self.receipts.repeat(connection, actor, 'ticket.create', key, fingerprint)
            if repeat: return repeat
            now = datetime.now(timezone.utc)
            row = dict(ticket_id=uuid4(), actor_id=actor.actor_id, data_scope=actor.data_scope, content=content,
                       status='created', revision=1, created_at=now, updated_at=now)
            connection.execute(insert(tickets).values(**row))
            result = view(row)
            self.receipts.finish(connection, actor, 'ticket.create', key, fingerprint, result)
            return result

    def read(self, actor: ActorContext, ticket_id: UUID) -> dict:
        with self.engine.connect() as connection:
            return view(self._read(connection, actor, ticket_id))

    def list(self, actor: ActorContext, cursor: int, limit: int) -> dict:
        with self.engine.connect() as connection:
            rows = connection.execute(select(tickets).where(tickets.c.data_scope == actor.data_scope)
                .order_by(tickets.c.created_at.desc(), tickets.c.ticket_id.desc()).offset(cursor).limit(limit+1)).mappings().all()
            return {'tickets': [view(row) for row in rows[:limit]], 'next_cursor': cursor+limit if len(rows)>limit else None}

    def edit(self, actor: ActorContext, ticket_id: UUID, content: dict, expected_revision: int, key: str) -> dict:
        fingerprint = digest(dict(ticket_id=str(ticket_id), content=content, expected_revision=expected_revision))
        with self.engine.begin() as connection:
            repeat = self.receipts.repeat(connection, actor, 'ticket.edit', key, fingerprint)
            if repeat: return repeat
            row = self._read(connection, actor, ticket_id, locked=True)
            if row['revision'] != expected_revision:
                raise CoreError('STALE_REVISION', 'Обращение изменилось; перечитайте текущую редакцию.')
            if row['status'] != 'created':
                raise CoreError('VALIDATION_ERROR', 'Отправленное обращение нельзя редактировать.')
            changed = {**row, 'content': content, 'revision': expected_revision+1, 'updated_at': datetime.now(timezone.utc)}
            connection.execute(update(tickets).where(tickets.c.ticket_id == ticket_id).values(
                content=content, revision=changed['revision'], updated_at=changed['updated_at']))
            result = view(changed)
            self.receipts.finish(connection, actor, 'ticket.edit', key, fingerprint, result)
            return result
