"""PostgreSQL chat storage and a narrow durable queue, ordered per conversation."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from uuid import UUID, uuid4

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.engine import Engine

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.modules.chat.infrastructure.tables import chat_sessions, chat_turns, chat_actions
from bervel.modules.chat.infrastructure.actions import view as action_view
from bervel.core.infrastructure.documents import PostgresDocumentRepository
from bervel.core.infrastructure.commands import CommandReceipts, digest, lock
from bervel.modules.chat.workflow import initial_workflow, public_workflow, validate_transition


def worker_lock(session_id: UUID) -> int:
    return int.from_bytes(hashlib.sha256(('chat-worker:' + str(session_id)).encode()).digest()[:8], 'big', signed=True)


def turn_view(row, actions=None) -> dict:
    return {**{key: row[key] for key in ('question', 'answer', 'state', 'sources', 'error')},
            'turn_id': str(row['turn_id']), 'created_at': row['created_at'].isoformat(),
            'results': row.get('results') or [], 'actions': actions or [],
            'workflow': public_workflow(row.get('workflow'))}


class PostgresChatRepository:
    def __init__(self, engine: Engine, documents: PostgresDocumentRepository, *, mode: str = 'assistant'):
        if mode not in {'assistant', 'inspection'}:
            raise ValueError('Unsupported chat mode')
        self.engine = engine
        self.documents = documents
        self.mode = mode
        self.command_prefix = 'chat' if mode == 'assistant' else 'inspection'
        self.receipts = CommandReceipts()

    def _session(self, connection, actor: ActorContext, session_id: UUID):
        row = connection.execute(select(chat_sessions).where(chat_sessions.c.session_id == session_id,
            chat_sessions.c.mode == self.mode,
            chat_sessions.c.data_scope == actor.data_scope, chat_sessions.c.actor_id == actor.actor_id)).mappings().first()
        if row is None:
            raise CoreError('NOT_FOUND', 'Chat session not found')
        return row

    def _view(self, connection, row) -> dict:
        turns = connection.execute(select(chat_turns).where(chat_turns.c.session_id == row['session_id'])
                                   .order_by(chat_turns.c.ordinal)).mappings()
        return {'session_id': str(row['session_id']), 'created_at': row['created_at'].isoformat(),
                'turns': [turn_view(turn, [action_view(action) for action in connection.execute(select(chat_actions)
                    .where(chat_actions.c.turn_id==turn['turn_id'], chat_actions.c.actor_id==row['actor_id'],
                        chat_actions.c.data_scope==row['data_scope']).order_by(chat_actions.c.ordinal)).mappings()]) for turn in turns]}

    def create(self, actor: ActorContext, key: str) -> dict:
        fingerprint = digest({})
        with self.engine.begin() as connection:
            repeat = self.receipts.repeat(connection, actor, self.command_prefix + '.create', key, fingerprint)
            if repeat:
                return self._view(connection, self._session(connection, actor, UUID(repeat['session_id'])))
            row = {'session_id': uuid4(), 'mode': self.mode, 'actor_id': actor.actor_id, 'data_scope': actor.data_scope,
                   'created_at': datetime.now(timezone.utc)}
            connection.execute(insert(chat_sessions).values(**row))
            self.receipts.finish(connection, actor, self.command_prefix + '.create', key, fingerprint, {'session_id': str(row['session_id'])})
            return self._view(connection, row)

    def read(self, actor: ActorContext, session_id: UUID) -> dict:
        with self.engine.connect() as connection:
            return self._view(connection, self._session(connection, actor, session_id))

    def list_sessions(self, actor: ActorContext, limit: int, offset: int) -> dict:
        # Only summary columns are selected; snapshots, results and actions stay in storage.
        belongs = chat_turns.c.session_id == chat_sessions.c.session_id
        title = select(chat_turns.c.question).where(belongs).order_by(chat_turns.c.ordinal).limit(1).scalar_subquery()
        count = select(func.count()).select_from(chat_turns).where(belongs).scalar_subquery()
        pending = select(chat_turns.c.turn_id).where(belongs, chat_turns.c.state.in_(['queued', 'running'])).exists()
        with self.engine.connect() as connection:
            rows = connection.execute(select(chat_sessions.c.session_id, chat_sessions.c.created_at,
                title.label('title'), count.label('turn_count'), pending.label('has_pending')).where(
                    chat_sessions.c.actor_id == actor.actor_id, chat_sessions.c.data_scope == actor.data_scope,
                    chat_sessions.c.mode == self.mode)
                .order_by(chat_sessions.c.created_at.desc(), chat_sessions.c.session_id.desc())
                .limit(limit + 1).offset(offset)).mappings().all()
        return {'sessions': [{'session_id': str(row['session_id']), 'created_at': row['created_at'].isoformat(),
                'title': (row['title'] or '').strip()[:120] or 'Новый диалог',
                'turn_count': row['turn_count'], 'has_pending': row['has_pending']} for row in rows[:limit]],
                'next_offset': offset + limit if len(rows) > limit else None}

    def delete(self, actor: ActorContext, session_id: UUID, key: str) -> dict:
        fingerprint = digest({'session_id': str(session_id)})
        with self.engine.begin() as connection:
            repeat = self.receipts.repeat(connection, actor, self.command_prefix + '.delete', key, fingerprint)
            if repeat:
                return repeat
            lock(connection, 'chat-enqueue:' + str(session_id))
            self._session(connection, actor, session_id)
            owns_worker = connection.execute(text('SELECT pg_try_advisory_xact_lock(:key)'),
                {'key': worker_lock(session_id)}).scalar()
            if not owns_worker or connection.execute(select(chat_turns.c.turn_id).where(
                    chat_turns.c.session_id == session_id, chat_turns.c.state.in_(['queued', 'running'])).limit(1)).first():
                raise CoreError('CONFLICT', 'Chat session has active work')
            turns = select(chat_turns.c.turn_id).where(chat_turns.c.session_id == session_id)
            connection.execute(delete(chat_actions).where(chat_actions.c.turn_id.in_(turns),
                chat_actions.c.actor_id == actor.actor_id, chat_actions.c.data_scope == actor.data_scope))
            connection.execute(delete(chat_turns).where(chat_turns.c.session_id == session_id))
            connection.execute(delete(chat_sessions).where(chat_sessions.c.session_id == session_id))
            result = {'session_id': str(session_id), 'deleted': True}
            self.receipts.finish(connection, actor, self.command_prefix + '.delete', key, fingerprint, result)
            return result

    def enqueue(self, actor: ActorContext, session_id: UUID, key: str, question: str) -> dict:
        fingerprint = digest({'session_id': str(session_id), 'question': question})
        with self.engine.begin() as connection:
            self._session(connection, actor, session_id)
            repeat = self.receipts.repeat(connection, actor, self.command_prefix + '.send', key, fingerprint)
            # Deletion shares this lock; validate again after a stale pre-lock read.
            lock(connection, 'chat-enqueue:' + str(session_id))
            self._session(connection, actor, session_id)
            if repeat:
                return turn_view(connection.execute(select(chat_turns).where(
                    chat_turns.c.turn_id == UUID(repeat['turn_id']))).mappings().one())
            # The enqueue lock permits new commands while a model is running.
            ordinal = connection.execute(select(func.coalesce(func.max(chat_turns.c.ordinal), 0)).where(
                chat_turns.c.session_id == session_id)).scalar_one() + 1
            row = {'turn_id': uuid4(), 'session_id': session_id, 'ordinal': ordinal, 'question': question,
                   'answer': None, 'error': None, 'state': 'queued',
                   'snapshot': self.documents.catalog(connection, actor.data_scope) if self.mode == 'assistant' else [],
                   'sources': [], 'results': [], 'workflow': initial_workflow(),
                   'actor_permissions': sorted(actor.permissions), 'created_at': datetime.now(timezone.utc)}
            connection.execute(insert(chat_turns).values(**row))
            self.receipts.finish(connection, actor, self.command_prefix + '.send', key, fingerprint, {'turn_id': str(row['turn_id'])})
            return turn_view(row)

    @contextmanager
    def claim(self):
        # Transaction-scoped locks release on crash. The durable running row remains;
        # a later owner records unknown outcome rather than repeating a model request.
        with self.engine.begin() as owner:
            candidates = owner.execute(select(chat_turns.c.session_id).join(chat_sessions).where(
                chat_sessions.c.mode == self.mode, chat_turns.c.state.in_(['queued', 'running']))
                .order_by(chat_turns.c.created_at, chat_turns.c.turn_id)).scalars().all()
            for session_id in dict.fromkeys(candidates):
                if not owner.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': worker_lock(session_id)}).scalar():
                    continue
                with self.engine.begin() as connection:
                    row = connection.execute(select(chat_turns, chat_sessions.c.actor_id, chat_sessions.c.data_scope)
                        .join(chat_sessions).where(chat_turns.c.session_id == session_id,
                            chat_sessions.c.mode == self.mode,
                            chat_turns.c.state.in_(['queued', 'running']))
                        .order_by(chat_turns.c.ordinal).with_for_update(of=chat_turns)).mappings().first()
                    if row is None:
                        continue
                    claimed = {**row, 'interrupted': row['state'] == 'running'}
                    if not claimed['interrupted']:
                        connection.execute(update(chat_turns).where(chat_turns.c.turn_id == row['turn_id']).values(state='running'))
                yield claimed
                return
            yield None

    def checkpoint(self, turn_id: UUID, workflow: dict) -> None:
        with self.engine.begin() as connection:
            row = connection.execute(select(chat_turns.c.state, chat_turns.c.workflow)
                .where(chat_turns.c.turn_id == turn_id).with_for_update()).mappings().first()
            if row is None or row['state'] != 'running':
                raise CoreError('STALE_ATTEMPT', 'Chat turn no longer owns checkpoint')
            validate_transition(row['workflow'], workflow)
            connection.execute(update(chat_turns).where(chat_turns.c.turn_id == turn_id,
                chat_turns.c.state == 'running').values(workflow=workflow))

    def finish(self, turn_id: UUID, *, answer: str | None = None, model: str | None = None,
               sources: list[dict] | None = None, error: str | None = None, results: list[dict] | None = None,
               outcome: str | None = None, workflow: dict | None = None) -> None:
        with self.engine.begin() as connection:
            row = connection.execute(select(chat_turns.c.state, chat_turns.c.workflow)
                .where(chat_turns.c.turn_id == turn_id).with_for_update()).mappings().first()
            if row is None or row['state'] != 'running':
                raise CoreError('STALE_ATTEMPT', 'Chat turn no longer owns completion')
            state = outcome if outcome is not None else ('failed' if error else 'completed')
            if state not in {'completed', 'partial', 'blocked', 'failed'}:
                raise CoreError('VALIDATION_ERROR', 'Chat outcome must be terminal')
            values = dict(answer=answer, model=model, sources=sources or [], error=error, results=results or [], state=state)
            if workflow is not None:
                validate_transition(row['workflow'], workflow)
                if workflow.get('phase') != state:
                    raise CoreError('VALIDATION_ERROR', 'Chat outcome must match its final phase')
                values['workflow'] = workflow
            result = connection.execute(update(chat_turns).where(chat_turns.c.turn_id == turn_id,
                chat_turns.c.state == 'running').values(**values))
            if result.rowcount != 1:
                raise CoreError('STALE_ATTEMPT', 'Chat turn no longer owns completion')

