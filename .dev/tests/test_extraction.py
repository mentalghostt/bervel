from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import pytest

from test_postgres_storage import command as upload

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/core-stage-3.1'


def command(ref, **changes):
    from bervel.core.models import ExtractArtifact
    return ExtractArtifact(artifact_ref=ref, command_key='extract-1', **changes)


def setup(services):
    from bervel.core.services import ExtractionService
    artifact, repo, store, registry, actor = services
    service = ExtractionService(artifact, repo, registry)
    first = artifact.register(actor, upload((FIXTURES / 'requirements-v1.csv').read_bytes()))
    return artifact, repo, actor, service, first


def test_two_dataset_revisions_provenance_and_exact_old_values(services):
    artifact, repo, actor, service, first = setup(services)
    result = service.extract(actor, command(first))
    assert service.extract(actor, command(first)) == result
    envelope = service.read(actor, result)
    assert envelope['quality_state'] == 'PARTIAL'
    assert envelope['schema_version'] == '1.0.0'
    assert envelope['producer']['module_version'] == '1.0.0'
    assert envelope['configuration_revision'] == 1
    assert envelope['observed_at'] is None
    assert envelope['input_snapshot']['inputs'] == [{'artifact_id': str(first.artifact_id), 'revision': 1, 'role': 'test_fixture'}]
    old = service.records(actor, result, 0, 1)
    assert old['records'][0]['payload']['quantity']['value'] == '10'
    assert old['next_cursor'] == 1
    second_page = service.records(actor, result, 1, 1)
    assert second_page['records'][0]['payload']['quantity']['value'] is None
    assert second_page['next_cursor'] is None
    second = artifact.register(actor, replace(upload((FIXTURES / 'requirements-v2.csv').read_bytes()),
        artifact_id=first.artifact_id, expected_revision=1, command_key='upload-2'))
    revised = service.extract(actor, replace(command(second), dataset_id=result.dataset_id,
        expected_revision=1, command_key='extract-2'))
    assert revised.revision == 2
    assert service.records(actor, revised, 0, 100)['records'][1]['payload']['quantity']['value'] == '4'
    assert service.records(actor, result, 0, 100)['records'][1]['payload']['quantity']['value'] is None
    assert len(artifact.history(actor, first.artifact_id)['datasets']) == 2
    other = replace(actor, actor_id='other', data_scope='test-area-b')
    from bervel.core.errors import CoreError
    for operation in [lambda: service.read(other, result), lambda: service.records(other, result, 0, 100),
                      lambda: service.extract(other, command(first))]:
        with pytest.raises(CoreError, match='NOT_FOUND'):
            operation()


def test_concurrent_repeat_and_changed_input_or_competing_revision(services):
    from bervel.core.errors import CoreError
    artifact, repo, actor, service, first = setup(services)
    with ThreadPoolExecutor(max_workers=4) as executor:
        refs = list(executor.map(lambda _: service.extract(actor, command(first)), range(4)))
    assert len(set(refs)) == 1
    with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
        service.extract(actor, replace(command(first), module_version='1.0.0', dataset_id=refs[0].dataset_id, expected_revision=1))
    def add(number):
        try:
            return service.extract(actor, replace(command(first), dataset_id=refs[0].dataset_id,
                expected_revision=1, command_key=f'competing-{number}'))
        except CoreError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(add, [1, 2]))
    assert results.count('STALE_REVISION') == 1
    assert len(artifact.history(actor, first.artifact_id)['datasets']) == 2


def test_invalid_csv_and_invalid_schema_publish_nothing(services, monkeypatch):
    from sqlalchemy import text
    from bervel.core.errors import CoreError
    from bervel.core.models import DataRecord, ExtractionResult
    from bervel.core.services import ExtractionService
    artifact, repo, store, registry, actor = services
    service = ExtractionService(artifact, repo, registry)
    ref = artifact.register(actor, upload(b'line,name,quantity,unit\n1,A,NaN,pcs\n'))
    with pytest.raises(CoreError, match='VALIDATION_ERROR'):
        service.extract(actor, command(ref))
    assert artifact.read(actor, ref).content.endswith(b'NaN,pcs\n')
    valid = artifact.register(actor, replace(upload((FIXTURES / 'requirements-v1.csv').read_bytes()), command_key='valid'))
    module = registry.get('demo.document-review', '1.0.0')
    invalid_module = replace(module, parser=lambda *_: ExtractionResult('demo.document-review.requirement-row', '1.0.0',
        'COMPLETE', [DataRecord('bad', 'COMPLETE', {'name': 'A', 'quantity': 'unknown'}, [], [])]))
    monkeypatch.setattr(registry, 'get', lambda *_: invalid_module)
    with pytest.raises(CoreError, match='VALIDATION_ERROR'):
        service.extract(actor, command(valid))
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM dataset_revisions')).scalar() == 0
        assert connection.execute(text('SELECT count(*) FROM process_runs')).scalar() == 0


def test_sql_failure_mid_publication_rolls_back_all_parts(services):
    from sqlalchemy import event, text
    artifact, repo, actor, service, first = setup(services)
    old = service.extract(actor, command(first))
    second = artifact.register(actor, replace(upload((FIXTURES / 'requirements-v2.csv').read_bytes()),
        artifact_id=first.artifact_id, expected_revision=1, command_key='upload-2'))
    called = 0
    def fail_after_records(connection, cursor, statement, parameters, context, executemany):
        nonlocal called
        if statement.startswith('INSERT INTO data_records'):
            called += 1
            if called == 2:
                raise RuntimeError('Injected mid-publication failure')
    event.listen(repo.engine, 'before_cursor_execute', fail_after_records)
    revised_command = replace(command(second), dataset_id=old.dataset_id, expected_revision=1, command_key='extract-2')
    try:
        with pytest.raises(RuntimeError, match='Injected'):
            service.extract(actor, revised_command)
    finally:
        event.remove(repo.engine, 'before_cursor_execute', fail_after_records)
    assert service.records(actor, old, 0, 100)['records'][1]['payload']['quantity']['value'] is None
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM dataset_revisions')).scalar() == 1
        assert connection.execute(text('SELECT count(*) FROM data_records')).scalar() == 2
        assert connection.execute(text('SELECT count(*) FROM input_snapshots')).scalar() == 1
        assert connection.execute(text('SELECT count(*) FROM process_runs')).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM command_receipts WHERE operation='dataset.extract'")).scalar() == 1
        assert connection.execute(text("SELECT count(*) FROM audit WHERE operation='dataset.extract'")).scalar() == 1
    assert service.extract(actor, revised_command).revision == 2


def test_schema_collision_is_rejected_in_database(services):
    from bervel.core.errors import CoreError
    artifact, repo, store, registry, actor = services
    definitions = registry.schemas.definitions()
    definitions[('demo.document-review.requirement-row', '1.0.0')]['properties']['name']['minLength'] = 2
    with pytest.raises(CoreError, match='UNSUPPORTED_SCHEMA'):
        repo.register_schemas(definitions)
