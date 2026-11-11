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
from bervel.modules.chat.workflow import initial_workflow, public_workflow, validate_transition, advance


def worker_lock(session_id: UUID) -> int:
    return int.from_bytes(hashlib.sha256(('chat-worker:' + str(session_id)).encode()).digest()[:8], 'big', signed=True)


def turn_view(row, actions=None, *, inspection=False) -> dict:
    native = (row.get('workflow') or {}).get('native') or {}
    return {**{key: row[key] for key in ('question', 'answer', 'state', 'sources', 'error')},
            'turn_id': str(row['turn_id']), 'created_at': row['created_at'].isoformat(),
            'results': row.get('results') or [], 'actions': [*(actions or []), *native.get('actions', [])],
            'workflow': public_workflow(row.get('workflow')), 'cancel_requested': row.get('cancel_requested', False),
            **({'followup_of': str(row['followup_of']) if row.get('followup_of') else None,
                'delivery_state': row.get('delivery_state')} if inspection else {}),
            **({'progress_text': native.get('progress_text'), 'cli_thread_id': native.get('thread_id')} if native else {})}


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
        result = {'session_id': str(row['session_id']), 'created_at': row['created_at'].isoformat(),
                'turns': [turn_view(turn, [action_view(action) for action in connection.execute(select(chat_actions)
                    .where(chat_actions.c.turn_id==turn['turn_id'], chat_actions.c.actor_id==row['actor_id'],
                        chat_actions.c.data_scope==row['data_scope']).order_by(chat_actions.c.ordinal)).mappings()],
                    inspection=self.mode == 'inspection') for turn in turns]}
        if self.mode == 'inspection':
            result.update(engine='codex-cli', cli_thread_id=next((turn['cli_thread_id'] for turn in reversed(result['turns']) if turn.get('cli_thread_id')), None))
        return result

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

    def read(self, actor: ActorContext, session_id: UUID, *, pending_command_key: str | None = None) -> dict:
        with self.engine.connect() as connection:
            row = self._session(connection, actor, session_id)
            if pending_command_key is None or self.mode != 'inspection':
                return self._view(connection, row)
            receipt = self.receipts.read_result(connection, actor, self.command_prefix + '.send', pending_command_key)
            result = self._view(connection, row)
            turn_id = receipt.get('turn_id') if receipt else None
            # An actor can own several sessions, and the receipt key is scoped to the actor.
            result['accepted_turn_id'] = turn_id if any(turn['turn_id'] == turn_id for turn in result['turns']) else None
            return result

    def _inspection_turn(self, connection, actor, session_id, turn_id, *, for_update=False):
        if self.mode != 'inspection':
            raise CoreError('FORBIDDEN', 'Остановка доступна только для «Проверки».')
        self._session(connection, actor, session_id)
        statement = select(chat_turns).where(chat_turns.c.session_id == session_id, chat_turns.c.turn_id == turn_id)
        row = connection.execute(statement.with_for_update() if for_update else statement).mappings().first()
        if row is None:
            raise CoreError('NOT_FOUND', 'Chat turn not found')
        return row

    def cancel_requested(self, actor: ActorContext, session_id: UUID, turn_id: UUID) -> bool:
        with self.engine.connect() as connection:
            return self._inspection_turn(connection, actor, session_id, turn_id)['cancel_requested']

    @contextmanager
    def action_guard(self, actor: ActorContext, session_id: UUID, turn_id: UUID):
        # The short gate serializes accepting an action with Stop. Release before
        # external execution so cancellation never waits for a long test/model call.
        # An operation accepted before Stop may finish; no later claim can pass.
        with self.engine.begin() as connection:
            lock(connection, 'inspection-action:' + str(turn_id))
            row = self._inspection_turn(connection, actor, session_id, turn_id, for_update=True)
            if row['cancel_requested'] or row['state'] != 'running':
                raise CoreError('INSPECTION_CANCELLED', 'Проверка остановлена. Новые действия не запускаются.')
        yield

    def cancel(self, actor: ActorContext, session_id: UUID, turn_id: UUID, key: str) -> dict:
        fingerprint = digest({'session_id': str(session_id), 'turn_id': str(turn_id)})
        with self.engine.begin() as connection:
            self._inspection_turn(connection, actor, session_id, turn_id)
            repeat = self.receipts.repeat(connection, actor, 'inspection.cancel', key, fingerprint)
            if repeat:
                return repeat
            lock(connection, 'chat-enqueue:' + str(session_id))
            lock(connection, 'inspection-action:' + str(turn_id))
            row = self._inspection_turn(connection, actor, session_id, turn_id, for_update=True)
            if row['state'] in {'queued', 'running'}:
                values = {'cancel_requested': True}
                if row['state'] == 'queued':
                    values.update(state='blocked', answer='Проверка остановлена пользователем до начала исполнения.',
                        workflow=advance(row['workflow'] or initial_workflow(unlimited_research=True), 'blocked', 'Остановлено пользователем.'))
                connection.execute(update(chat_turns).where(chat_turns.c.turn_id == turn_id).values(**values))
                row = {**row, **values}
                children = connection.execute(select(chat_turns).where(chat_turns.c.followup_of == turn_id,
                    chat_turns.c.session_id == session_id, chat_turns.c.delivery_state.in_(['pending', 'sending']))).mappings().all()
                for child in children:
                    child_values = {'cancel_requested': True}
                    if child['delivery_state'] == 'pending':
                        child_values.update(state='blocked', error='Уточнение не доставлено: проверка остановлена.')
                    connection.execute(update(chat_turns).where(chat_turns.c.turn_id == child['turn_id']).values(**child_values))
            actions = [action_view(action) for action in connection.execute(select(chat_actions).where(
                chat_actions.c.turn_id == turn_id, chat_actions.c.actor_id == actor.actor_id,
                chat_actions.c.data_scope == actor.data_scope).order_by(chat_actions.c.ordinal)).mappings()]
            result = turn_view(row, actions, inspection=True)
            self.receipts.finish(connection, actor, 'inspection.cancel', key, fingerprint, result)
            return result

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
                'title': ((row['title'] or '') if self.mode == 'inspection' else (row['title'] or '').strip()[:120]) or 'Новый диалог',
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
                    chat_turns.c.turn_id == UUID(repeat['turn_id']))).mappings().one(), inspection=self.mode == 'inspection')
            # The enqueue lock permits new commands while a model is running.
            ordinal = connection.execute(select(func.coalesce(func.max(chat_turns.c.ordinal), 0)).where(
                chat_turns.c.session_id == session_id)).scalar_one() + 1
            parent = None
            if self.mode == 'inspection':
                parent = connection.execute(select(chat_turns.c.turn_id).where(chat_turns.c.session_id == session_id,
                    chat_turns.c.state.in_(['queued', 'running']), chat_turns.c.followup_of.is_(None),
                    chat_turns.c.cancel_requested.is_(False)).order_by(chat_turns.c.ordinal).limit(1)).scalar()
            row = {'turn_id': uuid4(), 'session_id': session_id, 'ordinal': ordinal, 'question': question,
                   'answer': None, 'error': None, 'state': 'queued',
                   'snapshot': self.documents.catalog(connection, actor.data_scope) if self.mode == 'assistant' else [],
                   'sources': [], 'results': [], 'workflow': initial_workflow(unlimited_research=self.mode == 'inspection'),
                   'cancel_requested': False,
                   'followup_of': parent, 'delivery_state': 'pending' if parent else None,
                   'actor_permissions': sorted(actor.permissions), 'created_at': datetime.now(timezone.utc)}
            connection.execute(insert(chat_turns).values(**row))
            self.receipts.finish(connection, actor, self.command_prefix + '.send', key, fingerprint, {'turn_id': str(row['turn_id'])})
            return turn_view(row, inspection=self.mode == 'inspection')

    @contextmanager
    def claim(self):
        # Transaction-scoped locks release on crash. The durable running row remains;
        # a later owner records unknown outcome rather than repeating a model request.
        with self.engine.begin() as owner:
            candidates = owner.execute(select(chat_turns.c.session_id).join(chat_sessions).where(
                chat_sessions.c.mode == self.mode, chat_turns.c.state.in_(['queued', 'running']), chat_turns.c.followup_of.is_(None))
                .order_by(chat_turns.c.created_at, chat_turns.c.turn_id)).scalars().all()
            for session_id in dict.fromkeys(candidates):
                if not owner.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': worker_lock(session_id)}).scalar():
                    continue
                with self.engine.begin() as connection:
                    row = connection.execute(select(chat_turns, chat_sessions.c.actor_id, chat_sessions.c.data_scope)
                        .join(chat_sessions).where(chat_turns.c.session_id == session_id,
                            chat_sessions.c.mode == self.mode,
                            chat_turns.c.followup_of.is_(None),
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

    def pending_followups(self, actor: ActorContext, session_id: UUID, parent_id: UUID) -> list[dict]:
        with self.engine.connect() as connection:
            self._inspection_turn(connection, actor, session_id, parent_id)
            return [dict(row) for row in connection.execute(select(chat_turns).where(
                chat_turns.c.session_id == session_id, chat_turns.c.followup_of == parent_id,
                chat_turns.c.state.in_(['queued', 'running']), chat_turns.c.delivery_state.in_(['pending', 'sending']))
                .order_by(chat_turns.c.ordinal)).mappings()]

    def mark_followup_sending(self, actor: ActorContext, session_id: UUID, parent_id: UUID, message_id: UUID) -> bool:
        with self.engine.begin() as connection:
            lock(connection, 'chat-enqueue:' + str(session_id))
            parent = self._inspection_turn(connection, actor, session_id, parent_id)
            child = self._inspection_turn(connection, actor, session_id, message_id)
            if child['followup_of'] != parent_id:
                raise CoreError('NOT_FOUND', 'Inspection followup not found')
            if parent['cancel_requested'] or parent['state'] != 'running' or child['cancel_requested']:
                return False
            if child['delivery_state'] not in {'pending', 'sending'} or child['state'] not in {'queued', 'running'}:
                return False
            connection.execute(update(chat_turns).where(chat_turns.c.turn_id == message_id).values(delivery_state='sending'))
            return True

    def record_followup_delivery(self, actor: ActorContext, session_id: UUID, parent_id: UUID, message_id: UUID, state: str) -> None:
        if state not in {'queued', 'sending', 'accepted', 'rejected', 'unknown'}:
            raise CoreError('VALIDATION_ERROR', 'Unknown followup receipt state')
        with self.engine.begin() as connection:
            lock(connection, 'chat-enqueue:' + str(session_id))
            parent = self._inspection_turn(connection, actor, session_id, parent_id)
            child = self._inspection_turn(connection, actor, session_id, message_id)
            if child['followup_of'] != parent_id:
                raise CoreError('NOT_FOUND', 'Inspection followup not found')
            if child['delivery_state'] in {'accepted', 'unknown'}:
                return
            if state in {'queued', 'sending'}:
                return
            if state == 'accepted':
                values = {'state': 'completed', 'delivery_state': 'accepted', 'answer': None, 'error': None}
            elif state == 'unknown':
                values = {'state': 'failed', 'delivery_state': 'unknown',
                    'error': 'Исход доставки уточнения неизвестен. Автоматический повтор отключён.'}
            elif parent['cancel_requested'] or child['cancel_requested']:
                values = {'state': 'blocked', 'error': 'Уточнение не доставлено: проверка остановлена.'}
            else:
                values = {'state': 'queued', 'followup_of': None, 'delivery_state': None}
            connection.execute(update(chat_turns).where(chat_turns.c.turn_id == message_id).values(**values))

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
            session_id = connection.execute(select(chat_turns.c.session_id).where(chat_turns.c.turn_id == turn_id)).scalar()
            if self.mode == 'inspection' and session_id is not None:
                # Enqueue and terminal completion must observe the same parent state.
                lock(connection, 'chat-enqueue:' + str(session_id))
            row = connection.execute(select(chat_turns)
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
            if self.mode == 'inspection':
                children = connection.execute(select(chat_turns).where(chat_turns.c.followup_of == turn_id,
                    chat_turns.c.session_id == session_id, chat_turns.c.state.in_(['queued', 'running']))).mappings().all()
                for child in children:
                    if child['delivery_state'] == 'sending':
                        child_values = {'state': 'failed', 'delivery_state': 'unknown',
                            'error': 'Исход доставки уточнения неизвестен. Автоматический повтор отключён.'}
                    elif row['cancel_requested'] or state not in {'completed', 'partial'}:
                        child_values = {'state': 'blocked', 'error': 'Уточнение не доставлено: проверка завершена без продолжения.'}
                    else:
                        child_values = {'followup_of': None, 'delivery_state': None}
                    connection.execute(update(chat_turns).where(chat_turns.c.turn_id == child['turn_id']).values(**child_values))

