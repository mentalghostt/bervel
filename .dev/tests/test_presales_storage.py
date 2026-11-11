from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext


def actor(scope='presales-tests'):
    return ActorContext('manager', scope, frozenset({'settings:read', 'settings:write', 'artifact:read',
        'artifact:write', 'process:start', 'process:read', 'process:recover'}))


def settings():
    return {'target_margin_percent': '50', 'special_min_margin_percent': '70',
            'seller_contacts': '', 'offer_terms': ''}


def inputs(snapshot):
    return {'sources': [{'artifact_id': str(uuid4()), 'filename': 'request.csv', 'sha256': 'a' * 64}],
            'company': {'id': str(uuid4()), 'profile': {}, 'stock': {'data_kind': 'reported', 'rows': []}},
            'settings': snapshot['settings'], 'settings_snapshot_id': snapshot['snapshot_id']}


def test_scoped_settings_receipts_do_not_rollback_current_settings(pg_engine):
    from bervel.modules.presales.infrastructure.repository import PostgresPresalesRepository
    repo = PostgresPresalesRepository(pg_engine)
    first = repo.save_settings(actor(), None, settings(), 'first')
    second = repo.save_settings(actor(), first['snapshot_id'], {**settings(), 'target_margin_percent': '55'}, 'second')
    assert repo.save_settings(actor(), None, settings(), 'first') == first
    assert repo.read_settings(actor().data_scope) == second
    assert repo.read_settings('another-area') is None
    with pytest.raises(CoreError) as caught:
        repo.save_settings(actor(), None, {**settings(), 'seller_contacts': 'changed'}, 'first')
    assert caught.value.code == 'IDEMPOTENCY_CONFLICT'


def test_request_and_revision_intents_are_idempotent_and_history_is_frozen(pg_engine):
    from bervel.modules.presales.infrastructure.repository import PostgresPresalesRepository
    repo = PostgresPresalesRepository(pg_engine)
    snapshot = repo.save_settings(actor(), None, settings(), 'settings')
    original_inputs = inputs(snapshot)
    ids = [original_inputs['sources'][0]['artifact_id']]
    first = repo.create_request(actor(), ids, original_inputs, 'request')
    assert repo.create_request(actor(), ids, original_inputs, 'request') == first
    assert len(repo.list_requests(actor().data_scope)['items']) == 1
    with pytest.raises(CoreError):
        repo.read_request('other-area', UUID(first['request_id']))
    repo.finish_job(repo.claim(), state='complete')
    original = repo.read_revision(actor().data_scope, UUID(first['request_id']), UUID(first['revision_id']))
    changed_inputs = deepcopy(original_inputs)
    changed_inputs['settings']['target_margin_percent'] = '60'
    second = repo.create_revision(actor(), UUID(first['request_id']), UUID(first['revision_id']),
                                  'reanalyze', {}, changed_inputs, 'revision')
    assert second['revision_id'] != first['revision_id']
    assert repo.read_revision(actor().data_scope, UUID(first['request_id']), UUID(first['revision_id'])) == original
    assert repo.read_revision(actor().data_scope, UUID(first['request_id']), UUID(second['revision_id']))['inputs']['settings']['target_margin_percent'] == '60'


def test_job_stop_resume_and_late_owner_are_fenced(pg_engine):
    from bervel.modules.presales.infrastructure.repository import PostgresPresalesRepository
    repo = PostgresPresalesRepository(pg_engine)
    snapshot = repo.save_settings(actor(), None, settings(), 'settings')
    frozen = inputs(snapshot)
    receipt = repo.create_request(actor(), [frozen['sources'][0]['artifact_id']], frozen, 'start')
    owned = repo.claim()
    assert owned['job_id'] == receipt['job_id']
    assert repo.heartbeat(UUID(owned['job_id']), UUID(owned['lease_token'])) is False
    repo.stop_job(actor(), UUID(receipt['request_id']), UUID(receipt['job_id']), 'stop')
    assert repo.heartbeat(UUID(owned['job_id']), UUID(owned['lease_token'])) is True
    repo.finish_job(owned, state='stopped')
    repo.resume_job(actor(), UUID(receipt['request_id']), UUID(receipt['job_id']), 'resume')
    resumed = repo.claim()
    assert resumed['lease_token'] != owned['lease_token']
    with pytest.raises(CoreError) as caught:
        repo.checkpoint(owned, 'late-write', {'bad': True})
    assert caught.value.code == 'STALE_ATTEMPT'
    repo.checkpoint(resumed, 'good-write', {'ok': True})
    assert repo.read_job_cache(resumed)['good-write'] == {'ok': True}


def test_stop_and_worker_checkpoint_use_a_consistent_lock_order(pg_engine):
    from threading import Event, Thread, current_thread
    from sqlalchemy import event
    from bervel.modules.presales.infrastructure.repository import PostgresPresalesRepository
    repo = PostgresPresalesRepository(pg_engine)
    snapshot = repo.save_settings(actor(), None, settings(), 'settings')
    frozen = inputs(snapshot)
    receipt = repo.create_request(actor(), [frozen['sources'][0]['artifact_id']], frozen, 'start')
    owned = repo.claim()
    worker_ready, stop_attempted = Event(), Event()
    errors = []
    def coordinate(conn, cursor, statement, parameters, context, executemany):
        if current_thread().name == 'presales-test-worker' and statement.startswith('UPDATE presales_requests'):
            worker_ready.set()
            assert stop_attempted.wait(5)
        if current_thread().name == 'presales-test-stop' and 'FROM presales_jobs' in statement and 'FOR UPDATE' in statement:
            stop_attempted.set()
    def worker():
        try:
            repo.set_phase(owned, 'match', extracted={'title': 'Parsed', 'lines': [], 'lists': []})
        except Exception as error:
            errors.append(error)
    def stop():
        try:
            assert worker_ready.wait(5)
            repo.stop_job(actor(), UUID(receipt['request_id']), UUID(receipt['job_id']), 'stop')
        except Exception as error:
            errors.append(error)
    event.listen(pg_engine, 'before_cursor_execute', coordinate)
    first, second = Thread(target=worker, name='presales-test-worker'), Thread(target=stop, name='presales-test-stop')
    try:
        first.start(); second.start()
        first.join(10); second.join(10)
        assert not first.is_alive() and not second.is_alive()
        assert errors == []
    finally:
        stop_attempted.set()
        first.join(10); second.join(10)
        event.remove(pg_engine, 'before_cursor_execute', coordinate)
