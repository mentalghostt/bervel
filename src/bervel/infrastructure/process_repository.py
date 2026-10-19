from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import secrets
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update, text
from sqlalchemy.exc import SQLAlchemyError

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRevisionRef, DatasetRevisionRef, ExtractArtifact
from bervel.core.process_models import AttemptLease, EngineExecution, RecoverProcess, WorkflowDefinition
from .database import audit, input_snapshots, process_attempts, process_jobs, process_runs
from .repository import PostgresRepository, digest


def command_values(command: ExtractArtifact) -> dict:
    return {'artifact_id': str(command.artifact_ref.artifact_id), 'artifact_revision': command.artifact_ref.revision,
            'module_version': command.module_version, 'dataset_id': str(command.dataset_id) if command.dataset_id else None,
            'expected_revision': command.expected_revision}


class PostgresProcessRepository:
    def __init__(self, engine):
        self.engine = engine
        self.data = PostgresRepository(engine)

    @contextmanager
    def _transaction(self):
        try:
            with self.engine.begin() as connection:
                yield connection
        except SQLAlchemyError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Process storage is unavailable; no partial result was published') from error

    def _query(self):
        # READ COMMITTED gives a single snapshot to this whole statement, including
        # the correlated attempt history. Do not fetch attempts in a later SELECT.
        attempts = text("(SELECT jsonb_agg(to_jsonb(a) - 'process_run_id' ORDER BY a.number) "
                        "FROM process_attempts a WHERE a.process_run_id = process_jobs.process_run_id) AS attempts_snapshot")
        return select(process_jobs, process_runs.c.data_scope, process_runs.c.state,
                      process_runs.c.producer, process_runs.c.input_snapshot_id,
                      process_runs.c.created_at, attempts).join(process_runs)

    def _load(self, connection, run_id, scope=None, *, locked=False):
        query = self._query()
        query = query.where(process_jobs.c.process_run_id == UUID(str(run_id)))
        if scope is not None:
            query = query.where(process_runs.c.data_scope == scope)
        if locked:
            query = query.with_for_update(of=process_jobs)
        row = connection.execute(query).mappings().first()
        if row is None:
            raise CoreError('NOT_FOUND', 'Process not found')
        return row

    def _attempt(self, connection, row):
        return connection.execute(select(process_attempts).where(
            process_attempts.c.process_run_id == row['process_run_id'],
            process_attempts.c.number == row['active_attempt'])).mappings().one()

    def _view(self, connection, row):
        return {'process_run_id': str(row['process_run_id']), 'data_scope': row['data_scope'],
                'state': row['state'], 'active_attempt': row['active_attempt'], 'input': row['command'],
                'producer': row['producer'], 'workflow': row['workflow'], 'result': row['result'],
                'created_at': row['created_at'].isoformat(), 'updated_at': row['updated_at'].isoformat(),
                'attempts': row['attempts_snapshot'] or []}

    def lookup_start(self, actor, command):
        with self._transaction() as connection:
            receipt = self.data._repeat(connection, actor, 'process.start', command.command_key, digest(command_values(command)))
            return self._view(connection, self._load(connection, receipt['process_run_id'], actor.data_scope)) if receipt else None

    def create(self, actor, command, workflow: WorkflowDefinition, invocation_token: str, producer: dict):
        with self._transaction() as connection:
            fingerprint = digest(command_values(command))
            receipt = self.data._repeat(connection, actor, 'process.start', command.command_key, fingerprint)
            if receipt:
                return self._view(connection, self._load(connection, receipt['process_run_id'], actor.data_scope)), False
            self.data._artifact(connection, actor, command.artifact_ref)
            run_id, snapshot_id, now = uuid4(), uuid4(), datetime.now(timezone.utc)
            inputs = [{'artifact_id': str(command.artifact_ref.artifact_id), 'revision': command.artifact_ref.revision,
                       'role': 'test_fixture'}]
            connection.execute(insert(input_snapshots).values(input_snapshot_id=snapshot_id, data_scope=actor.data_scope, inputs=inputs))
            connection.execute(insert(process_runs).values(process_run_id=run_id, data_scope=actor.data_scope,
                input_snapshot_id=snapshot_id, producer=producer, configuration_revision=1, state='QUEUED', created_at=now))
            connection.execute(insert(process_jobs).values(process_run_id=run_id, actor_id=actor.actor_id,
                actor_permissions=sorted(actor.permissions), command=command_values(command), workflow=asdict(workflow),
                invocation_hash=hashlib.sha256(invocation_token.encode()).hexdigest(), active_attempt=1, updated_at=now))
            connection.execute(insert(process_attempts).values(process_run_id=run_id, number=1, state='DISPATCHING',
                created_at=now, updated_at=now))
            self.data._finish(connection, actor, 'process.start', command.command_key, fingerprint, {'process_run_id': str(run_id)})
            return self._view(connection, self._load(connection, run_id)), True

    def read(self, actor, run_id):
        with self._transaction() as connection:
            row = self._load(connection, run_id, actor.data_scope)
            self.data._artifact(connection, actor, ArtifactRevisionRef(UUID(row['command']['artifact_id']), row['command']['artifact_revision']))
            return self._view(connection, row)

    def list(self, actor, state=None, cursor=0, limit=20):
        with self._transaction() as connection:
            query = self._query().where(process_runs.c.data_scope == actor.data_scope)
            if state:
                query = query.where(process_runs.c.state == state)
            rows = list(connection.execute(query.order_by(process_runs.c.created_at, process_jobs.c.process_run_id)
                .offset(cursor).limit(limit + 1)).mappings())
            return {'processes': [self._view(connection, row) for row in rows[:limit]],
                    'next_cursor': cursor + limit if len(rows) > limit else None}

    def _check_execution(self, row, attempt, execution):
        if (execution.workflow_id != row['workflow']['workflow_id'] or execution.version_id != row['workflow']['version_id']):
            raise CoreError('WORKFLOW_MISMATCH', 'Execution does not use the pinned workflow version')
        if execution.retry_of != attempt['parent_execution_id'] or attempt['execution_id'] not in (None, execution.execution_id):
            raise CoreError('STALE_ATTEMPT', 'Execution does not belong to the active attempt')

    def claim(self, run_id, invocation_token, execution: EngineExecution) -> AttemptLease:
        with self._transaction() as connection:
            row = self._load(connection, run_id, locked=True)
            if not secrets.compare_digest(row['invocation_hash'], hashlib.sha256(invocation_token.encode()).hexdigest()):
                raise CoreError('FORBIDDEN', 'Callback is not bound to this process')
            attempt = self._attempt(connection, row)
            self._check_execution(row, attempt, execution)
            if not row['result']:
                if attempt['state'] == 'FAILED':
                    raise CoreError('STALE_ATTEMPT', 'Failed attempt requires explicit recovery')
                now = datetime.now(timezone.utc)
                connection.execute(update(process_attempts).where(process_attempts.c.process_run_id == row['process_run_id'],
                    process_attempts.c.number == row['active_attempt']).values(execution_id=execution.execution_id,
                        state='RUNNING', error_code=None, updated_at=now))
                connection.execute(update(process_runs).where(process_runs.c.process_run_id == row['process_run_id']).values(state='RUNNING'))
                connection.execute(update(process_jobs).where(process_jobs.c.process_run_id == row['process_run_id']).values(updated_at=now))
            actor = ActorContext(row['actor_id'], row['data_scope'], frozenset(row['actor_permissions']))
            command = row['command']
            extract = ExtractArtifact(ArtifactRevisionRef(UUID(command['artifact_id']), command['artifact_revision']),
                'process:' + str(row['process_run_id']), command['module_version'],
                UUID(command['dataset_id']) if command['dataset_id'] else None, command['expected_revision'])
            return AttemptLease(row['process_run_id'], row['active_attempt'], execution.execution_id, actor, extract, row['producer'], row['result'])

    def complete(self, lease, result, producer):
        with self._transaction() as connection:
            row = self._load(connection, lease.process_run_id, locked=True)
            attempt = self._attempt(connection, row)
            if row['active_attempt'] != lease.attempt or attempt['execution_id'] != lease.execution_id:
                raise CoreError('STALE_ATTEMPT', 'A newer attempt owns publication')
            if row['result']:
                return DatasetRevisionRef(UUID(row['result']['dataset_id']), row['result']['revision'])
            if attempt['state'] != 'RUNNING':
                raise CoreError('STALE_ATTEMPT', 'Attempt no longer owns publication')
            if producer != row['producer']:
                raise CoreError('WORKFLOW_MISMATCH', 'Result producer differs from the pinned producer')
            ref = self.data._publish_extraction(connection, lease.actor, lease.command, result, producer,
                (row['process_run_id'], row['input_snapshot_id']))
            now = datetime.now(timezone.utc)
            connection.execute(update(process_jobs).where(process_jobs.c.process_run_id == row['process_run_id']).values(
                result={'dataset_id': str(ref.dataset_id), 'revision': ref.revision}, updated_at=now))
            connection.execute(update(process_runs).where(process_runs.c.process_run_id == row['process_run_id']).values(state='COMPLETED'))
            connection.execute(update(process_attempts).where(process_attempts.c.process_run_id == row['process_run_id'],
                process_attempts.c.number == lease.attempt).values(state='COMPLETED', error_code=None, updated_at=now))
            return ref

    def fail(self, lease, code):
        with self._transaction() as connection:
            row = self._load(connection, lease.process_run_id, locked=True)
            attempt = self._attempt(connection, row)
            if row['result'] or row['active_attempt'] != lease.attempt or attempt['execution_id'] != lease.execution_id:
                return
            now = datetime.now(timezone.utc)
            connection.execute(update(process_runs).where(process_runs.c.process_run_id == row['process_run_id']).values(state='FAILED'))
            connection.execute(update(process_attempts).where(process_attempts.c.process_run_id == row['process_run_id'],
                process_attempts.c.number == lease.attempt).values(state='FAILED', error_code=code, updated_at=now))
            connection.execute(update(process_jobs).where(process_jobs.c.process_run_id == row['process_run_id']).values(updated_at=now))

    def dispatch_unknown(self, run_id, number):
        with self._transaction() as connection:
            row = self._load(connection, run_id, locked=True)
            attempt = self._attempt(connection, row)
            if row['result'] or row['active_attempt'] != number or attempt['execution_id'] is not None:
                return
            now = datetime.now(timezone.utc)
            connection.execute(update(process_runs).where(process_runs.c.process_run_id == row['process_run_id']).values(state='RECOVERY_REQUIRED'))
            connection.execute(update(process_attempts).where(process_attempts.c.process_run_id == row['process_run_id'],
                process_attempts.c.number == number).values(state='UNKNOWN', error_code='ENGINE_RESPONSE_UNKNOWN', updated_at=now))
            connection.execute(update(process_jobs).where(process_jobs.c.process_run_id == row['process_run_id']).values(updated_at=now))

    def observe(self, actor, run_id, execution):
        with self._transaction() as connection:
            row = self._load(connection, run_id, actor.data_scope, locked=True)
            if row['result']:
                return self._view(connection, row)
            attempt = self._attempt(connection, row)
            self._check_execution(row, attempt, execution)
            if attempt['state'] == 'FAILED':
                return self._view(connection, row)
            if execution.state in {'error', 'crashed', 'canceled'}:
                state, attempt_state, code = 'FAILED', 'FAILED', 'ENGINE_FAILED'
            elif execution.state in {'new', 'running', 'waiting'}:
                state, attempt_state, code = 'RUNNING', 'RUNNING', None
            else:
                state, attempt_state, code = 'RECOVERY_REQUIRED', 'UNKNOWN', 'ENGINE_RESULT_MISSING'
            now = datetime.now(timezone.utc)
            connection.execute(update(process_runs).where(process_runs.c.process_run_id == row['process_run_id']).values(state=state))
            connection.execute(update(process_attempts).where(process_attempts.c.process_run_id == row['process_run_id'],
                process_attempts.c.number == row['active_attempt']).values(execution_id=execution.execution_id,
                    state=attempt_state, error_code=code, updated_at=now))
            connection.execute(update(process_jobs).where(process_jobs.c.process_run_id == row['process_run_id']).values(updated_at=now))
            return self._view(connection, self._load(connection, run_id))

    def lookup_recovery(self, actor, run_id, command):
        with self._transaction() as connection:
            receipt = self.data._repeat(connection, actor, 'process.recover', command.command_key,
                digest({'process_run_id': str(run_id), **asdict(command)}))
            return self._view(connection, self._load(connection, receipt['process_run_id'], actor.data_scope)) if receipt else None

    def reserve_recovery(self, actor, run_id, command: RecoverProcess, execution):
        with self._transaction() as connection:
            fingerprint = digest({'process_run_id': str(run_id), **asdict(command)})
            receipt = self.data._repeat(connection, actor, 'process.recover', command.command_key, fingerprint)
            if receipt:
                return self._view(connection, self._load(connection, receipt['process_run_id'], actor.data_scope)), False
            row = self._load(connection, run_id, actor.data_scope, locked=True)
            if row['result']:
                return self._view(connection, row), False
            if row['active_attempt'] != command.expected_attempt:
                raise CoreError('STALE_ATTEMPT', 'Process attempt has changed')
            attempt = self._attempt(connection, row)
            self._check_execution(row, attempt, execution)
            if attempt['execution_id'] != execution.execution_id:
                raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Reconcile the execution before recovery')
            if execution.state not in {'error', 'crashed', 'canceled'} or execution.retry_success_id:
                raise CoreError('PROCESS_ACTIVE', 'Execution is active, successful or requires reconciliation')
            number, now = row['active_attempt'] + 1, datetime.now(timezone.utc)
            connection.execute(update(process_jobs).where(process_jobs.c.process_run_id == row['process_run_id']).values(active_attempt=number, updated_at=now))
            connection.execute(update(process_runs).where(process_runs.c.process_run_id == row['process_run_id']).values(state='QUEUED'))
            connection.execute(insert(process_attempts).values(process_run_id=row['process_run_id'], number=number,
                parent_execution_id=execution.execution_id, state='DISPATCHING', created_at=now, updated_at=now))
            self.data._finish(connection, actor, 'process.recover', command.command_key, fingerprint,
                {'process_run_id': str(run_id), 'attempt': number})
            connection.execute(insert(audit).values(audit_id=uuid4(), data_scope=actor.data_scope, actor_id=actor.actor_id,
                operation='process.recovery-reason', object_ref={'process_run_id': str(run_id), 'attempt': number,
                    'reason': command.reason}, created_at=now))
            return self._view(connection, self._load(connection, run_id)), True
