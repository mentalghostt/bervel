from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import pytest


def command(content=b'original', **changes):
    from bervel.core.models import RegisterArtifact
    return RegisterArtifact(content=content, filename='../../original.csv', media_type='text/csv',
                            source={'origin_system': 'manual-test', 'external_id': None, 'external_revision': None},
                            command_key='upload-1', **changes)


def test_artifact_ids_repeats_metadata_and_scope(services):
    from bervel.core.errors import CoreError
    artifact, repo, store, actor = services
    first = artifact.register(actor, command())
    assert artifact.register(actor, command()) == first
    read = artifact.read(actor, first)
    assert read.content == b'original'
    assert read.metadata['source']['origin_system'] == 'manual-test'
    second = artifact.register(actor, replace(command(b'changed'), command_key='upload-2'))
    assert second.artifact_id != first.artifact_id
    assert artifact.read(actor, first).content == b'original'
    assert artifact.read(actor, second).content == b'changed'
    with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
        artifact.register(actor, command(b'different'))
    with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
        artifact.register(actor, replace(command(), source={'origin_system': 'changed'}))
    other = replace(actor, actor_id='other', data_scope='test-area-b')
    with pytest.raises(CoreError, match='NOT_FOUND'):
        artifact.read(other, first)
    same_bytes_elsewhere = artifact.register(other, command())
    assert same_bytes_elsewhere.artifact_id != first.artifact_id
    assert artifact.read(other, same_bytes_elsewhere).content == b'original'


def test_same_content_new_key_creates_distinct_file_sharing_bytes(services):
    artifact, repo, store, actor = services
    first = artifact.register(actor, command())
    same = artifact.register(actor, replace(command(), command_key='same-content-new-key'))
    assert same != first
    assert artifact.metadata(actor, first)['object_ref'] == artifact.metadata(actor, same)['object_ref']
    assert {item['artifact_id'] for item in artifact.list(actor)['artifacts']} == {str(first.artifact_id),str(same.artifact_id)}


def test_concurrent_upload_repeats_and_independent_commands(services):
    artifact, repo, store, actor = services
    with ThreadPoolExecutor(max_workers=4) as executor:
        refs = list(executor.map(lambda _: artifact.register(actor, command()), range(4)))
    assert len(set(refs)) == 1
    def add(number):
        return artifact.register(actor, replace(command(str(number).encode()), command_key=f'compete-{number}'))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(add, [1, 2]))
    assert len(set(results)) == 2 and refs[0] not in results
    assert {artifact.read(actor, ref).content for ref in results} == {b'1',b'2'}
    assert artifact.read(actor,refs[0]).content == b'original'


def test_file_and_sql_failure_leave_no_accessible_file(services, monkeypatch):
    from sqlalchemy import event, text
    from bervel.core.errors import CoreError
    artifact, repo, store, actor = services
    def fail_write(_):
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Cannot save object')
    with monkeypatch.context() as patch:
        patch.setattr(store, 'put', fail_write)
        with pytest.raises(CoreError):
            artifact.register(actor, command())
    def fail_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO audit'):
            raise RuntimeError('Injected transaction failure')
    event.listen(repo.engine, 'before_cursor_execute', fail_audit)
    try:
        with pytest.raises(RuntimeError, match='Injected'):
            artifact.register(actor, command())
    finally:
        event.remove(repo.engine, 'before_cursor_execute', fail_audit)
    with repo.engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM artifacts')).scalar() == 0
        assert connection.execute(text('SELECT count(*) FROM command_receipts')).scalar() == 0
    assert artifact.read(actor,artifact.register(actor, command())).content == b'original'


def test_migration_repeat_and_permission_checks(services):
    from bervel.core.infrastructure.database import migrate
    from bervel.core.errors import CoreError
    artifact, repo, store, actor = services
    first = artifact.register(actor, command())
    migrate(repo.engine)
    assert artifact.read(actor, first).content == b'original'
    with pytest.raises(CoreError, match='FORBIDDEN'):
        artifact.register(replace(actor, permissions=frozenset()), command())
