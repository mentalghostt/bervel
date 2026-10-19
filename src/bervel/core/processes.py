import secrets

from .errors import CoreError
from .ports import ProcessRepository, WorkflowEngine
from .process_models import RecoverProcess
from .services import ExtractionService, authorize, validate_key

STATES = frozenset({'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'RECOVERY_REQUIRED'})


class ProcessService:
    def __init__(self, repository: ProcessRepository, extraction: ExtractionService, engine: WorkflowEngine):
        self.repository = repository
        self.extraction = extraction
        self.engine = engine

    def start(self, actor, command):
        authorize(actor, 'process:start')
        producer = self.extraction.validate_command(actor, command)
        existing = self.repository.lookup_start(actor, command)
        if existing:
            return existing
        original = self.extraction.artifacts.metadata(actor, command.artifact_ref)
        if original['media_type'] != 'text/csv':
            raise CoreError('VALIDATION_ERROR', 'Educational extraction supports text/csv only; original is preserved')
        workflow = self.engine.describe()
        token = secrets.token_urlsafe(32)
        process, created = self.repository.create(actor, command, workflow, token, producer)
        if created:
            try:
                self.engine.start(process['process_run_id'], token)
            except CoreError:
                self.repository.dispatch_unknown(process['process_run_id'], 1)
        return self.repository.read(actor, process['process_run_id'])

    def _read_permissions(self, actor):
        for permission in ('process:read', 'artifact:read', 'dataset:read'):
            authorize(actor, permission)

    def read(self, actor, run_id):
        self._read_permissions(actor)
        return self.repository.read(actor, run_id)

    def list(self, actor, state=None, cursor=0, limit=20):
        self._read_permissions(actor)
        if state is not None and state not in STATES or cursor < 0 or not 1 <= limit <= 100:
            raise CoreError('VALIDATION_ERROR', 'Invalid process filter or pagination')
        return self.repository.list(actor, state, cursor, limit)

    def _lease(self, run_id, execution_id, token):
        execution = self.engine.execution(execution_id)
        if (execution.process_run_id != str(run_id) or not execution.invocation_token or
                not secrets.compare_digest(execution.invocation_token.encode(), token.encode())):
            raise CoreError('WORKFLOW_MISMATCH', 'Execution input is not bound to this process')
        return self.repository.claim(run_id, token, execution)

    def claim(self, run_id, execution_id, token):
        lease = self._lease(run_id, execution_id, token)
        return {'process_run_id': str(run_id), 'execution_id': execution_id,
                'state': 'COMPLETED' if lease.result else 'RUNNING'}

    def execute(self, run_id, execution_id, token):
        lease = self._lease(run_id, execution_id, token)
        if lease.result:
            return lease.result
        try:
            result, producer = self.extraction.prepare(lease.actor, lease.command)
            ref = self.repository.complete(lease, result, producer)
            return {'dataset_id': str(ref.dataset_id), 'revision': ref.revision}
        except CoreError as error:
            self.repository.fail(lease, error.code)
            raise
        except Exception as error:
            self.repository.fail(lease, 'INTERNAL_ERROR')
            raise CoreError('INTERNAL_ERROR', 'Process operation failed') from error

    def reconcile(self, actor, run_id):
        authorize(actor, 'process:recover')
        process = self.read(actor, run_id)
        if process['result']:
            return process
        attempt = process['attempts'][-1]
        if attempt['execution_id']:
            execution = self.engine.execution(attempt['execution_id'])
        else:
            matches = self.engine.find(process['workflow']['workflow_id'], str(run_id), attempt['parent_execution_id'])
            if not matches:
                self.repository.dispatch_unknown(run_id, process['active_attempt'])
                return self.repository.read(actor, run_id)
            if len(matches) != 1:
                raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Multiple executions require operator investigation')
            execution = matches[0]
            self._lease(run_id, execution.execution_id, execution.invocation_token or '')
        return self.repository.observe(actor, run_id, execution)

    def recover(self, actor, run_id, command: RecoverProcess):
        authorize(actor, 'process:recover')
        self._read_permissions(actor)
        validate_key(command.command_key)
        if command.expected_attempt < 1 or not command.reason.strip() or len(command.reason) > 1000:
            raise CoreError('VALIDATION_ERROR', 'Recovery requires an expected attempt and a reason of 1..1000 characters')
        existing = self.repository.lookup_recovery(actor, run_id, command)
        if existing:
            return existing
        process = self.reconcile(actor, run_id)
        if process['result']:
            return process
        if process['active_attempt'] != command.expected_attempt:
            raise CoreError('STALE_ATTEMPT', 'Process attempt has changed')
        parent_id = process['attempts'][-1]['execution_id']
        if not parent_id:
            raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'The original execution is unknown; no blind retry is allowed')
        external = self.engine.execution(parent_id)
        children = self.engine.find(process['workflow']['workflow_id'], str(run_id), parent_id)
        if children:
            raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'An existing engine retry requires reconciliation')
        reserved, created = self.repository.reserve_recovery(actor, run_id, command, external)
        if created:
            try:
                self.engine.retry(parent_id)
            except CoreError:
                self.repository.dispatch_unknown(run_id, reserved['active_attempt'])
                return self.repository.read(actor, run_id)
            return self.reconcile(actor, run_id)
        return reserved
