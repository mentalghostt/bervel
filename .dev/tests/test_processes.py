from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.core.process_models import EngineExecution, RecoverProcess
from test_process_repository import setup_process


class FakeEngine:
    def __init__(self, workflow):
        self.workflow = workflow
        self.executions = {}
        self.starts = []
        self.retries = []
        self.after_start = None
        self.after_retry = None

    def describe(self):
        return self.workflow

    def start(self, process_id, token):
        self.starts.append(process_id)
        self.executions['100'] = EngineExecution('100', self.workflow.workflow_id, self.workflow.version_id,
            'running', process_run_id=process_id, invocation_token=token)
        if self.after_start:
            self.after_start()

    def execution(self, identifier):
        return self.executions[identifier]

    def find(self, workflow_id, process_id, retry_of=None):
        return [item for item in self.executions.values() if item.workflow_id == workflow_id
                and item.process_run_id == process_id and item.retry_of == retry_of]

    def retry(self, identifier):
        self.retries.append(identifier)
        child = replace(self.executions[identifier], execution_id=str(100 + len(self.retries)),
                        retry_of=identifier, state='running', retry_success_id=None)
        self.executions[child.execution_id] = child
        if self.after_retry:
            self.after_retry(child)
        return child


def process_service(services):
    from bervel.core.processes import ProcessService
    repository, actor, command, workflow, _, _, _, extraction = setup_process(services)
    engine = FakeEngine(workflow)
    service = ProcessService(repository, extraction, engine)
    return service, repository, engine, actor, replace(command, command_key='start-through-service')


def execute(service, engine, identifier='100'):
    execution = engine.execution(identifier)
    return service.execute(execution.process_run_id, identifier, execution.invocation_token)


def test_start_execute_and_replay_without_engine_call(services):
    service, repository, engine, actor, command = process_service(services)
    process = service.start(actor, command)
    assert process['state'] == 'QUEUED'
    first = execute(service, engine)
    assert execute(service, engine) == first
    assert service.read(actor, process['process_run_id'])['state'] == 'COMPLETED'
    engine.describe = lambda: pytest.fail('Replay must not require the engine')
    assert service.start(actor, command)['process_run_id'] == process['process_run_id']
    assert len(engine.starts) == 1
    assert repository.read(actor, process['process_run_id'])['result'] == first


def test_unknown_start_is_reconciled_without_redispatch(services):
    service, _, engine, actor, command = process_service(services)
    def lost():
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'response lost')
    engine.after_start = lost
    process = service.start(actor, command)
    assert process['state'] == 'RECOVERY_REQUIRED'
    reconciled = service.reconcile(actor, process['process_run_id'])
    assert reconciled['attempts'][0]['execution_id'] == '100'
    assert len(engine.starts) == 1
    execute(service, engine)
    assert service.read(actor, process['process_run_id'])['state'] == 'COMPLETED'


def test_original_version_retry_and_repeated_recovery(services):
    service, _, engine, actor, command = process_service(services)
    process = service.start(actor, command)
    service.claim(process['process_run_id'], '100', engine.execution('100').invocation_token)
    engine.executions['100'] = replace(engine.execution('100'), state='crashed')
    engine.workflow = replace(engine.workflow, version_id='published-v2')
    recover = RecoverProcess('recover', 1, 'worker crashed')
    recovered = service.recover(actor, process['process_run_id'], recover)
    assert recovered['active_attempt'] == 2
    assert engine.execution('101').version_id == 'published-v1'
    execute(service, engine, '101')
    assert service.recover(actor, process['process_run_id'], recover)['state'] == 'COMPLETED'
    assert engine.retries == ['100']
    with pytest.raises(CoreError, match='STALE_ATTEMPT'):
        execute(service, engine, '100')


def test_callback_before_lost_retry_response_keeps_committed_result(services):
    service, _, engine, actor, command = process_service(services)
    process = service.start(actor, command)
    service.claim(process['process_run_id'], '100', engine.execution('100').invocation_token)
    engine.executions['100'] = replace(engine.execution('100'), state='crashed')
    def complete_then_lose(child):
        execute(service, engine, child.execution_id)
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'retry response lost')
    engine.after_retry = complete_then_lose
    recovered = service.recover(actor, process['process_run_id'], RecoverProcess('recover', 1, 'crash'))
    assert recovered['state'] == 'COMPLETED'
    assert recovered['result'] is not None


def test_concurrent_distinct_recovery_commands_make_one_engine_retry(services):
    service, _, engine, actor, command = process_service(services)
    process = service.start(actor, command)
    service.claim(process['process_run_id'], '100', engine.execution('100').invocation_token)
    engine.executions['100'] = replace(engine.execution('100'), state='crashed')
    def recover(key):
        try:
            return service.recover(actor, process['process_run_id'], RecoverProcess(key, 1, 'crash'))['active_attempt']
        except CoreError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(recover, ['recover-a', 'recover-b']))
    assert outcomes.count(2) == 1
    assert len(engine.retries) == 1


def test_wrong_execution_input_scope_permissions_and_incomplete_history(services):
    service, _, engine, actor, command = process_service(services)
    with pytest.raises(CoreError, match='FORBIDDEN'):
        service.start(replace(actor, permissions=actor.permissions - {'process:start'}), command)
    process = service.start(actor, command)
    with pytest.raises(CoreError, match='NOT_FOUND'):
        service.read(replace(actor, data_scope='foreign'), process['process_run_id'])
    engine.executions['100'] = replace(engine.execution('100'), process_run_id=str(uuid4()))
    with pytest.raises(CoreError, match='WORKFLOW_MISMATCH'):
        service.execute(process['process_run_id'], '100', engine.execution('100').invocation_token)
    assert service.read(actor, process['process_run_id'])['state'] == 'QUEUED'
    with pytest.raises(CoreError, match='ENGINE_RESPONSE_UNKNOWN'):
        service.recover(actor, process['process_run_id'], RecoverProcess('recover', 1, 'unknown'))


def test_invalid_input_records_failure_preserves_original(services):
    from bervel.core.models import RegisterArtifact
    service, repository, engine, actor, command = process_service(services)
    artifacts = service.extraction.artifacts
    ref = artifacts.register(actor, RegisterArtifact(b'line,name,quantity,unit\n1,A,NaN,pcs\n',
        'bad.csv', 'text/csv', {}, 'invalid-upload'))
    process = service.start(actor, replace(command, artifact_ref=ref))
    with pytest.raises(CoreError, match='VALIDATION_ERROR'):
        execute(service, engine)
    read = repository.read(actor, process['process_run_id'])
    assert read['state'] == 'FAILED' and read['result'] is None
    assert read['attempts'][0]['error_code'] == 'VALIDATION_ERROR'
    assert artifacts.read(actor, ref).content.endswith(b'NaN,pcs\n')
    reconciled = service.reconcile(actor, process['process_run_id'])
    assert reconciled['state'] == 'FAILED'
    assert reconciled['attempts'][0]['error_code'] == 'VALIDATION_ERROR'
