"""PostgreSQL ownership and decision serialization, independent from file writes."""
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select, insert, update
from bervel.core.errors import CoreError
from bervel.core.infrastructure.commands import digest, lock
from bervel.modules.inspection.infrastructure.engineering_tables import engineering_proposals as proposals


class PostgresEngineeringRepository:
    def __init__(self, engine, session_reader): self.engine, self.session_reader = engine, session_reader

    def _session(self, connection, actor, session_id, turn_id=None):
        session = self.session_reader(actor, session_id)
        if turn_id is not None and not any(str(item['turn_id']) == str(turn_id) for item in session['turns']):
            raise CoreError('NOT_FOUND', 'Ход диалога не найден.')

    def check_session(self, actor, session_id, turn_id=None):
        with self.engine.connect() as connection: self._session(connection, actor, session_id, turn_id)

    def _row(self, connection, actor, proposal_id):
        row = connection.execute(select(proposals).where(proposals.c.proposal_id == proposal_id,
            proposals.c.actor_id == actor.actor_id, proposals.c.data_scope == actor.data_scope)).mappings().first()
        if row is None: raise CoreError('NOT_FOUND', 'Предложение не найдено.')
        self._session(connection, actor, row['session_id'])
        return row

    def _card(self, row):
        return {**row['card'], 'status': 'unknown' if row['status'] == 'applying' else row['status']}

    def create(self, actor, session_id, turn_id, ordinal, metadata, prepared):
        fingerprint = digest({'session_id': str(session_id), 'turn_id': str(turn_id), 'ordinal': ordinal, **metadata, **prepared})
        with self.engine.begin() as connection:
            self._session(connection, actor, session_id, turn_id)
            lock(connection, 'chat-enqueue:' + str(session_id))
            self._session(connection, actor, session_id, turn_id)
            lock(connection, 'inspection-proposal:' + str(turn_id) + ':' + str(ordinal))
            existing = connection.execute(select(proposals).where(proposals.c.turn_id == turn_id, proposals.c.ordinal == ordinal)).mappings().first()
            if existing:
                if existing['fingerprint'] != fingerprint: raise CoreError('IDEMPOTENCY_CONFLICT', 'Предложение уже существует с другим содержимым.')
                return self._card(existing)
            identifier = uuid4()
            card = {'kind': 'code_proposal', 'proposal_id': str(identifier), 'session_id': str(session_id), **metadata,
                'fingerprint': fingerprint, 'diff': prepared['diff'], 'files': [item['path'] for item in prepared['changes']]}
            row = {'proposal_id': identifier, 'session_id': session_id, 'turn_id': turn_id, 'ordinal': ordinal,
                'actor_id': actor.actor_id, 'data_scope': actor.data_scope, 'fingerprint': fingerprint,
                'payload': prepared, 'card': card, 'status': 'pending', 'created_at': datetime.now(timezone.utc)}
            connection.execute(insert(proposals).values(**row))
            return self._card(row)

    def read(self, actor, proposal_id):
        with self.engine.connect() as connection: return self._card(self._row(connection, actor, proposal_id))

    @contextmanager
    def claim(self, actor, proposal_id, command_key, fingerprint, decision):
        # Separate durable state transaction survives process/network loss during file IO.
        with self.engine.begin() as owner:
            lock(owner, 'inspection-confirm:' + str(proposal_id))
            lock(owner, 'inspection-command:' + actor.data_scope + ':' + actor.actor_id + ':' + command_key)
            replay = owner.execute(select(proposals.c.proposal_id).where(proposals.c.actor_id == actor.actor_id,
                proposals.c.data_scope == actor.data_scope, proposals.c.command_key == command_key,
                proposals.c.proposal_id != proposal_id)).first()
            if replay: raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ подтверждения уже использован для другого предложения.')
            row = self._row(owner, actor, proposal_id)
            lock(owner, 'chat-enqueue:' + str(row['session_id']))
            with self.engine.begin() as connection:
                row = self._row(connection, actor, proposal_id)
                if row['fingerprint'] != fingerprint: raise CoreError('STALE_REVISION', 'Отпечаток предложения не совпадает.')
                if row['command_key'] is not None:
                    if row['command_key'] != command_key or row['decision'] != decision:
                        raise CoreError('IDEMPOTENCY_CONFLICT', 'Предложение уже получило другое решение.')
                    execute = False
                    if row['status'] == 'applying':
                        connection.execute(update(proposals).where(proposals.c.proposal_id == proposal_id).values(status='unknown'))
                else:
                    connection.execute(update(proposals).where(proposals.c.proposal_id == proposal_id).values(
                        command_key=command_key, decision=decision, status='applying'))
                    execute = True
            yield {'execute': execute, 'payload': row['payload']}

    def finish(self, proposal_id, status):
        if status not in {'applied', 'rejected', 'failed', 'unknown'}: raise ValueError('Invalid proposal outcome')
        with self.engine.begin() as connection:
            connection.execute(update(proposals).where(proposals.c.proposal_id == proposal_id,
                proposals.c.status == 'applying').values(status=status))
