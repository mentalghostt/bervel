from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from threading import Event, current_thread

import pytest
from sqlalchemy import event, text

from bervel.core.errors import CoreError
from bervel.core.models import ExtractArtifact, RegisterArtifact
from bervel.core.services import ExtractionService

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/core-stage-3.1'


def setup_process(services):
    from bervel.core.process_models import WorkflowDefinition
    from bervel.infrastructure.process_repository import PostgresProcessRepository
    artifacts, data, _, registry, actor = services
    actor = replace(actor, permissions=actor.permissions | {'process:start', 'process:read', 'process:recover'})
    ref = artifacts.register(actor, RegisterArtifact((FIXTURES / 'requirements-v1.csv').read_bytes(),
        'requirements.csv', 'text/csv', {'origin_system': 'stage32-test'}, 'upload'))
    command = ExtractArtifact(ref, 'start')
    repository = PostgresProcessRepository(data.engine)
    workflow = WorkflowDefinition('workflow-demo', 'published-v1', '1.0.0')
    producer = {'module_id': 'demo.document-review', 'module_version': '1.0.0',
                'workflow_type': 'demo.document-review.parse', 'workflow_version': '1.0.0'}
    process, created = repository.create(actor, command, workflow, 'private-invocation-token', producer)
    assert created
    extraction = ExtractionService(artifacts, data, registry)
    result = registry.get('demo.document-review', '1.0.0').parser((FIXTURES / 'requirements-v1.csv').read_bytes(), ref)
    return repository, actor, command, workflow, process, result, producer, extraction


def execution(identifier='100', parent=None, version='published-v1', state='running'):
    from bervel.core.process_models import EngineExecution
    return EngineExecution(identifier, 'workflow-demo', version, state, parent)


def test_start_repeat_conflict_scope_and_snapshot(services):
    repository, actor, command, workflow, process, *_ = setup_process(services)
    again, created = repository.create(actor, command, replace(workflow, version_id='published-v2'), 'different-token', {})
    assert not created
    assert again['process_run_id'] == process['process_run_id']
    assert again['workflow']['version_id'] == 'published-v1'
    assert again['input']['artifact_revision'] == 1
    with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
        repository.create(actor, replace(command, expected_revision=1), workflow, 'token', {})
    with pytest.raises(CoreError, match='NOT_FOUND'):
        repository.read(replace(actor, data_scope='foreign'), process['process_run_id'])
    assert 'invocation_token' not in str(repository.read(actor, process['process_run_id']))
    with repository.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM process_runs')).scalar() == 1


def test_publish_is_atomic_repeat_uses_same_run_and_source(services):
    repository, actor, command, _, process, result, producer, extraction = setup_process(services)
    lease = repository.claim(process['process_run_id'], 'private-invocation-token', execution())
    first = repository.complete(lease, result, producer)
    assert repository.complete(lease, result, producer) == first
    read = repository.read(actor, process['process_run_id'])
    assert read['state'] == 'COMPLETED'
    assert read['result'] == {'dataset_id': str(first.dataset_id), 'revision': 1}
    assert read['attempts'][0]['state'] == 'COMPLETED'
    assert extraction.read(actor, first)['process_run_id'] == process['process_run_id']
    with repository.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM process_runs')).scalar() == 1
        assert connection.execute(text('SELECT count(*) FROM dataset_revisions')).scalar() == 1
        assert connection.execute(text('SELECT count(*) FROM data_records')).scalar() == 2


def test_duplicate_callbacks_publish_once_concurrently(services):
    repository, _, _, _, process, result, producer, _ = setup_process(services)
    lease = repository.claim(process['process_run_id'], 'private-invocation-token', execution())
    with ThreadPoolExecutor(max_workers=3) as executor:
        refs = list(executor.map(lambda _: repository.complete(lease, result, producer), range(3)))
    assert len(set(refs)) == 1


def test_publication_failure_rolls_back_process_and_result(services):
    repository, actor, _, _, process, result, producer, _ = setup_process(services)
    lease = repository.claim(process['process_run_id'], 'private-invocation-token', execution())
    def fail_rows(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO data_records'):
            raise RuntimeError('injected publication interruption')
    event.listen(repository.engine, 'before_cursor_execute', fail_rows)
    try:
        with pytest.raises(RuntimeError, match='injected'):
            repository.complete(lease, result, producer)
    finally:
        event.remove(repository.engine, 'before_cursor_execute', fail_rows)
    assert repository.read(actor, process['process_run_id'])['state'] == 'RUNNING'
    with repository.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM dataset_revisions')).scalar() == 0
        assert connection.execute(text('SELECT count(*) FROM data_records')).scalar() == 0
    assert repository.complete(lease, result, producer).revision == 1


def test_recovery_fences_late_worker_and_keeps_original_version(services):
    from bervel.core.process_models import RecoverProcess
    repository, actor, _, _, process, result, producer, _ = setup_process(services)
    old = repository.claim(process['process_run_id'], 'private-invocation-token', execution())
    repository.observe(actor, process['process_run_id'], execution(state='crashed'))
    recovered, created = repository.reserve_recovery(actor, process['process_run_id'],
        RecoverProcess('recover-1', 1, 'worker crashed'), execution(state='crashed'))
    assert created and recovered['active_attempt'] == 2
    with pytest.raises(CoreError, match='STALE_ATTEMPT'):
        repository.complete(old, result, producer)
    with pytest.raises(CoreError, match='WORKFLOW_MISMATCH'):
        repository.claim(process['process_run_id'], 'private-invocation-token', execution('101', '100', 'published-v2'))
    with pytest.raises(CoreError, match='STALE_ATTEMPT'):
        repository.claim(process['process_run_id'], 'private-invocation-token', execution('102', 'unrelated'))
    current = repository.claim(process['process_run_id'], 'private-invocation-token', execution('101', '100'))
    ref = repository.complete(current, result, producer)
    assert ref.revision == 1
    assert repository.read(actor, process['process_run_id'])['workflow']['version_id'] == 'published-v1'


def test_concurrent_recovery_one_winner_repeat_and_conflict(services):
    from bervel.core.process_models import RecoverProcess
    repository, actor, _, _, process, *_ = setup_process(services)
    repository.claim(process['process_run_id'], 'private-invocation-token', execution())
    external = execution(state='crashed')
    def recover(key):
        try:
            return repository.reserve_recovery(actor, process['process_run_id'], RecoverProcess(key, 1, 'crash'), external)[1]
        except CoreError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(recover, ['recovery-a', 'recovery-b']))
    assert results.count(True) == 1 and results.count('STALE_ATTEMPT') == 1
    winner = ['recovery-a', 'recovery-b'][results.index(True)]
    assert recover(winner) is False
    with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
        repository.reserve_recovery(actor, process['process_run_id'], RecoverProcess(winner, 1, 'different'), external)


def test_active_unknown_and_forged_callbacks_do_not_advance(services):
    from bervel.core.process_models import RecoverProcess
    repository, actor, _, _, process, *_ = setup_process(services)
    with pytest.raises(CoreError, match='FORBIDDEN'):
        repository.claim(process['process_run_id'], 'wrong-token', execution())
    repository.claim(process['process_run_id'], 'private-invocation-token', execution())
    with pytest.raises(CoreError, match='PROCESS_ACTIVE'):
        repository.reserve_recovery(actor, process['process_run_id'], RecoverProcess('retry', 1, 'test'), execution())
    repository.dispatch_unknown(process['process_run_id'], 1)
    assert repository.read(actor, process['process_run_id'])['state'] == 'RUNNING'
    with pytest.raises(CoreError, match='STALE_ATTEMPT'):
        repository.claim(process['process_run_id'], 'private-invocation-token', execution('101'))


def test_sync_command_cannot_capture_async_publication_receipt(services):
    repository, actor, command, _, process, result, producer, extraction = setup_process(services)
    synchronous = extraction.extract(actor, replace(command, command_key='process:' + process['process_run_id']))
    lease = repository.claim(process['process_run_id'], 'private-invocation-token', execution())
    asynchronous = repository.complete(lease, result, producer)
    assert asynchronous != synchronous
    assert extraction.read(actor, asynchronous)['process_run_id'] == process['process_run_id']


def test_newer_dataset_revision_rejects_late_process(services):
    repository, actor, command, workflow, _, result, producer, extraction = setup_process(services)
    first = extraction.extract(actor, replace(command, command_key='initial-sync'))
    revised = replace(command, dataset_id=first.dataset_id, expected_revision=1, command_key='async-revision')
    process, _ = repository.create(actor, revised, workflow, 'token', producer)
    lease = repository.claim(process['process_run_id'], 'token', execution())
    latest = extraction.extract(actor, replace(revised, command_key='newer-sync'))
    assert latest.revision == 2
    with pytest.raises(CoreError, match='STALE_REVISION'):
        repository.complete(lease, result, producer)
    with repository.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM dataset_revisions WHERE dataset_id=:id'),
                                  {'id': first.dataset_id}).scalar() == 2


def test_read_returns_consistent_process_and_attempt_during_publication(services, monkeypatch):
    repository, actor, _, _, process, result, producer, _ = setup_process(services)
    lease = repository.claim(process['process_run_id'], 'private-invocation-token', execution())
    loaded, resume = Event(), Event()
    original_load = repository._load
    def paused_load(connection, run_id, scope=None, *, locked=False):
        row = original_load(connection, run_id, scope, locked=locked)
        if current_thread().name.startswith('snapshot-reader') and not locked:
            loaded.set()
            assert resume.wait(10)
        return row
    monkeypatch.setattr(repository, '_load', paused_load)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='snapshot-reader') as executor:
        future = executor.submit(repository.read, actor, process['process_run_id'])
        assert loaded.wait(10)
        try:
            repository.complete(lease, result, producer)
        finally:
            resume.set()
        read = future.result(timeout=10)
    assert read['state'] == read['attempts'][-1]['state']
    assert (read['result'] is not None) == (read['state'] == 'COMPLETED')
