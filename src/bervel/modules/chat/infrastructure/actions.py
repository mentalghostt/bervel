"""Commit intent before a possible effect; interrupted effects are never replayed."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Engine

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.core.infrastructure.commands import digest, lock
from bervel.modules.chat.infrastructure.tables import chat_actions


def view(row) -> dict:
    return {**{k: row[k] for k in ('operation', 'arguments', 'state', 'result', 'error')}, 'action_id': str(row['action_id'])}


class PostgresActionReceipts:
    def __init__(self, engine: Engine):
        self.engine = engine

    def begin(self, actor: ActorContext, turn_id: UUID, ordinal: int, operation: str, arguments: dict) -> dict:
        fingerprint = digest({'operation': operation, 'arguments': arguments})
        with self.engine.begin() as connection:
            lock(connection, f'assistant:{actor.data_scope}:{actor.actor_id}:{turn_id}:{ordinal}')
            row = connection.execute(select(chat_actions).where(chat_actions.c.turn_id == turn_id,
                chat_actions.c.ordinal == ordinal, chat_actions.c.actor_id == actor.actor_id,
                chat_actions.c.data_scope == actor.data_scope)).mappings().first()
            if row:
                if row['fingerprint'] != fingerprint:
                    raise CoreError('IDEMPOTENCY_CONFLICT', 'Действие уже сохранено с другими параметрами.')
                if row['state'] == 'running':
                    connection.execute(update(chat_actions).where(chat_actions.c.action_id == row['action_id']).values(state='unknown'))
                    return {**view(row), 'state': 'unknown'}
                return view(row)
            identifier = uuid4()
            connection.execute(insert(chat_actions).values(action_id=identifier, turn_id=turn_id, ordinal=ordinal,
                actor_id=actor.actor_id, data_scope=actor.data_scope, operation=operation, arguments=arguments,
                fingerprint=fingerprint, state='running', created_at=datetime.now(timezone.utc)))
            return {'action_id': str(identifier), 'state': 'new'}

    def finish(self, actor: ActorContext, action_id: UUID, *, result: dict | None = None, error: dict | None = None,
               unknown: bool = False) -> None:
        with self.engine.begin() as connection:
            changed = connection.execute(update(chat_actions).where(chat_actions.c.action_id == action_id,
                chat_actions.c.actor_id == actor.actor_id, chat_actions.c.data_scope == actor.data_scope,
                chat_actions.c.state == 'running').values(state='unknown' if unknown else 'failed' if error else 'completed', result=result,
                    error=error, finished_at=datetime.now(timezone.utc)))
            if changed.rowcount != 1:
                raise CoreError('ACTION_OUTCOME_UNKNOWN', 'Исход действия неизвестен; автоматический повтор отключён.')

    def read(self, actor: ActorContext, turn_id: UUID) -> list[dict]:
        with self.engine.connect() as connection:
            return [view(row) for row in connection.execute(select(chat_actions).where(
                chat_actions.c.turn_id == turn_id, chat_actions.c.actor_id == actor.actor_id,
                chat_actions.c.data_scope == actor.data_scope).order_by(chat_actions.c.ordinal)).mappings()]

    def interrupt(self, actor: ActorContext, turn_id: UUID) -> None:
        with self.engine.begin() as connection:
            connection.execute(update(chat_actions).where(chat_actions.c.turn_id == turn_id,
                chat_actions.c.actor_id == actor.actor_id, chat_actions.c.data_scope == actor.data_scope,
                chat_actions.c.state == 'running').values(state='unknown', finished_at=datetime.now(timezone.utc)))
