from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
from bervel.modules.ai_tender_radar.infrastructure.runs import analyses, legacy_groups, pages, reports, runs
from test_ai_radar_runs import API, SETTINGS, command, completed, enqueue, run_scenario
from test_named_radar_execution import Source, tender
from test_radar_unified_contract import legacy_job


def _rows_for_run(scenario, run_id):
    with scenario.engine.connect() as connection:
        children = [connection.execute(select(table).where(table.c.run_id == run_id)).all()
                    for table in (runs, analyses, pages, reports)]
        run_jobs = connection.execute(select(jobs).where(
            (jobs.c.job_id == run_id) | (jobs.c.operation['run_id'].astext == str(run_id)))).all()
    return [len(rows) for rows in [*children, run_jobs]]


def _delete(scenario, run_id, *, key='a'):
    return scenario.client.delete(f'{API}/{run_id}', headers={'Authorization': 'Bearer ' + key * 32})


def test_delete_legacy_removes_only_frozen_jobs_in_its_own_scope(run_scenario):
    s = run_scenario
    repository = s.service.run_pipeline.repository
    frozen = legacy_job(s, key='frozen')
    repository.freeze_legacy(s.actor.data_scope)
    outside_archive = legacy_job(s, key='later')
    owner = s.actor
    s.actor = ActorContext('outsider', 'other-scope', owner.permissions)
    other_scope = legacy_job(s, key='foreign')
    repository.freeze_legacy(s.actor.data_scope)
    s.actor = owner
    named = UUID(enqueue(s)['run_id'])
    assert [card['job_id'] for card in s.client.get(f'{API}/legacy').json()['cards']] == [str(frozen)]
    # Membership alone cannot grant access to another scope or a named run's job.
    with s.engine.begin() as connection:
        connection.execute(legacy_groups.update().where(legacy_groups.c.data_scope == owner.data_scope)
            .values(job_ids=list(map(str, [frozen, other_scope, named]))))

    assert _delete(s, 'legacy', key='c').status_code == 403
    assert repository.legacy_entry(owner.data_scope) is not None
    assert _delete(s, 'legacy').status_code == 204
    assert _delete(s, 'legacy').status_code == 204
    assert s.client.get(f'{API}/legacy').status_code == 404
    assert [entry['id'] for entry in s.client.get(f'{API}/history').json()['entries']] == [str(named)]
    with s.engine.connect() as connection:
        found = set(connection.execute(select(jobs.c.job_id)).scalars())
    assert found == {outside_archive, other_scope, named}
    assert [card['job_id'] for card in repository.legacy_cards('other-scope')['cards']] == [other_scope]
    assert s.client.get(SETTINGS).json()['snapshot'] == s.snapshot


def test_delete_legacy_rolls_back_if_a_member_is_active(run_scenario):
    s = run_scenario
    frozen = legacy_job(s, key='frozen')
    active = legacy_job(s, key='active-member')
    repository = s.service.run_pipeline.repository
    repository.freeze_legacy(s.actor.data_scope)
    with s.engine.begin() as connection:
        connection.execute(jobs.update().where(jobs.c.job_id == active).values(state='queued'))

    assert _delete(s, 'legacy').status_code == 409
    assert repository.legacy_entry(s.actor.data_scope)['card_count'] == 2
    assert s.service.jobs.read(s.actor.data_scope, frozen)['state'] == 'loaded'
    assert s.service.jobs.read(s.actor.data_scope, active)['state'] == 'queued'


def test_delete_removes_one_run_and_all_owned_records(run_scenario):
    s = run_scenario
    receipt, run = completed(s)
    run_id = UUID(receipt['run_id'])
    question = s.client.post(f'{API}/{run_id}/question', json={
        'command_key': 'question-before-delete', 'analysis_id': run['cards'][0]['analysis_id'],
        'question': 'Почему этот тендер выгоден?',
    })
    assert question.status_code == 202
    assert s.service.run_once()
    repository = s.service.run_pipeline.repository
    owned_reports = repository.read_reports(run_id)
    assert {(row['job_id'], row['data']['kind']) for row in owned_reports} == {
        (run_id, 'rating'), (UUID(question.json()['job_id']), 'question')}
    assert _rows_for_run(s, run_id) == [1, 3, 4, 2, 2]

    s.source.remaining.update(search=4, purchase=3)
    other_receipt, other_run = completed(s, command(s, 'other-run'))
    other = UUID(other_receipt['run_id'])
    other_reports = repository.read_reports(other)
    assert {(row['job_id'], row['data']['kind']) for row in other_reports} == {(other, 'rating')}
    assert _rows_for_run(s, other) == [1, 3, 4, 1, 1]
    legacy_job = str(uuid4())
    with s.engine.begin() as connection:
        connection.execute(insert(legacy_groups).values(data_scope=s.actor.data_scope, job_ids=[legacy_job]))
    assert _delete(s, run_id, key='c').status_code == 403
    assert _delete(s, run_id, key='b').status_code == 204
    assert _rows_for_run(s, run_id) == [1, 3, 4, 2, 2]

    assert _delete(s, run_id).status_code == 204
    assert _rows_for_run(s, run_id) == [0, 0, 0, 0, 0]
    assert repository.read_reports(run_id) == []
    assert _rows_for_run(s, other) == [1, 3, 4, 1, 1]
    assert repository.read_reports(other) == other_reports
    assert s.client.get(f'{API}/{other}').json() == other_run
    assert s.client.get(f'{API}/{run_id}').status_code == 404
    assert [entry['id'] for entry in s.client.get(f'{API}/history').json()['entries']] == ['legacy', str(other)]
    assert s.client.get(f'{API}/legacy').status_code == 200
    assert s.client.get(SETTINGS).json()['snapshot'] == s.snapshot
    assert _delete(s, run_id).status_code == 204


def test_delete_blocks_queued_run_but_allows_failed_current_job(run_scenario):
    s = run_scenario
    run_id = UUID(enqueue(s)['run_id'])
    assert _delete(s, run_id).status_code == 409
    assert _rows_for_run(s, run_id) == [1, 0, 0, 0, 1]
    assert s.service.jobs.claim()['job_id'] == run_id
    s.service.jobs.fail(run_id, CoreError('INTERNAL_ERROR', 'Тестовый сбой'))
    assert s.client.get(f'{API}/{run_id}').json()['data']['state'] == 'failed'
    assert s.client.get(f'{API}/history').json()['entries'][0]['state'] == 'failed'
    assert _delete(s, run_id).status_code == 204
    assert _rows_for_run(s, run_id) == [0, 0, 0, 0, 0]


@pytest.mark.parametrize('kind', ['question', 'audit', 'reanalyze'])
def test_delete_waits_for_related_queued_job(run_scenario, kind):
    s = run_scenario
    receipt, run = completed(s)
    run_id = UUID(receipt['run_id'])
    body = {'command_key': f'{kind}-while-deleting'}
    if kind in {'question', 'reanalyze'}:
        body['analysis_id'] = run['cards'][0]['analysis_id']
    if kind == 'question':
        body['question'] = 'Почему такой балл?'
    response = s.client.post(f'{API}/{run_id}/{kind}', json=body)
    assert response.status_code == 202
    assert _delete(s, run_id).status_code == 409
    assert s.service.run_once()
    assert _delete(s, run_id).status_code == 204
    assert _rows_for_run(s, run_id) == [0, 0, 0, 0, 0]
    if kind == 'reanalyze':
        child_id = UUID(response.json()['run_id'])
        assert s.client.get(f'{API}/{child_id}').status_code == 200
        assert _rows_for_run(s, child_id)[-1] == 1


def test_operation_enqueue_rechecks_run_under_lock(run_scenario, monkeypatch):
    s = run_scenario
    receipt, run = completed(s)
    run_id = UUID(receipt['run_id'])
    repository = s.service.run_pipeline.repository
    enqueue_operation = repository.enqueue_operation

    def delete_after_operation_read(*args, **kwargs):
        repository.delete(s.actor.data_scope, run_id)
        return enqueue_operation(*args, **kwargs)

    monkeypatch.setattr(repository, 'enqueue_operation', delete_after_operation_read)
    question = s.client.post(f'{API}/{run_id}/question', json={
        'command_key': 'delayed-question', 'analysis_id': run['cards'][0]['analysis_id'],
        'question': 'Почему такой балл?',
    })
    assert question.status_code == 404
    assert _rows_for_run(s, run_id) == [0, 0, 0, 0, 0]


def test_stop_queued_run_without_claiming_or_deleting_it(run_scenario):
    s = run_scenario
    run_id = UUID(enqueue(s)['run_id'])
    url = f'{API}/{run_id}/stop'
    assert s.client.post(url, headers={'Authorization': 'Bearer ' + 'c' * 32}).status_code == 403
    assert s.client.post(url, headers={'Authorization': 'Bearer ' + 'b' * 32}).status_code == 404
    assert s.client.post(url).status_code == 204
    assert s.client.post(url).status_code == 204
    assert s.service.run_once() is False
    assert _rows_for_run(s, run_id) == [1, 0, 0, 0, 1]
    status = s.client.get(f'{API}/{run_id}').json()['data']
    assert status['state'] == 'failed'
    assert status['error']['code'] == status['stop_reason'] == 'USER_STOPPED'
    assert s.client.get(f'{API}/history').json()['entries'][0]['stop_reason'] == 'USER_STOPPED'
    assert s.client.post(f'{API}/{run_id}/resume', json={'command_key': 'resume-stopped'}).status_code == 202


@pytest.mark.parametrize('worker_exit', ['finish', 'lease_expires'])
def test_stop_running_run_preserves_results_and_waits_for_worker(run_scenario, worker_exit):
    s = run_scenario
    receipt, completed_run = completed(s)
    run_id = UUID(receipt['run_id'])
    repository = s.service.run_pipeline.repository
    root_job = s.service.jobs.read(s.actor.data_scope, run_id)
    saved_cards, saved_pages = repository.cards(run_id), repository.source_pages(run_id)
    with s.engine.begin() as connection:
        connection.execute(jobs.update().where(jobs.c.job_id == run_id).values(state='running',
            lease_until=root_job['updated_at'] + timedelta(minutes=5)))
    stored_data = repository.read(s.actor.data_scope, run_id)['data']
    repository.update(s.actor.data_scope, run_id, {**stored_data, 'state': 'running'})
    assert s.client.post(f'{API}/{run_id}/stop').status_code == 204
    assert s.client.get(f'{API}/{run_id}').json()['data']['stop_requested'] is True
    assert s.client.get(f'{API}/history').json()['entries'][0]['stop_requested'] is True
    assert s.service.jobs.read(s.actor.data_scope, run_id)['state'] == 'running'
    assert _delete(s, run_id).status_code == 409
    assert s.client.post(f'{API}/{run_id}/resume', json={'command_key': 'too-early'}).status_code == 409
    with pytest.raises(CoreError, match='USER_STOPPED'):
        s.service.jobs.heartbeat(run_id)
    if worker_exit == 'finish':
        # The worker can return after its final heartbeat while the request is arriving.
        s.service.jobs.finish_operation(run_id)
    else:
        with s.engine.begin() as connection:
            connection.execute(jobs.update().where(jobs.c.job_id == run_id).values(
                lease_until=root_job['updated_at'] - timedelta(minutes=1)))
        assert s.service.jobs.claim() is None
    stopped = s.client.get(f'{API}/{run_id}').json()
    assert stopped['data']['state'] == 'failed'
    assert stopped['data']['error']['code'] == 'USER_STOPPED'
    assert stopped['data']['checkpoint'] == completed_run['data']['checkpoint']
    assert repository.cards(run_id) == saved_cards
    assert repository.source_pages(run_id) == saved_pages
    assert s.client.post(f'{API}/{run_id}/resume', json={'command_key': 'after-stop'}).status_code == 202


def test_named_worker_stops_after_current_source_request(run_scenario, monkeypatch):
    s = run_scenario
    source = Source([tender('one', name='Болт М12'), tender('two', name='Болт М12')], detail_quota=2)
    s.service.run_pipeline.sources['test_api'] = source
    response = s.client.post(API, json={'settings_snapshot_id': s.snapshot['id'], 'command_key': 'named-stop',
        'name': 'Остановка в источнике', 'source': 'test_api'})
    assert response.status_code == 202
    url = f"{API}/{response.json()['run_id']}"
    fetch = source.fetch_tender

    def stop_during_fetch(key, search_item):
        assert s.client.post(f'{url}/stop').status_code == 204
        return fetch(key, search_item)

    monkeypatch.setattr(source, 'fetch_tender', stop_during_fetch)
    assert s.service.run_once()
    saved = s.client.get(url).json()
    assert saved['data']['stop_reason'] == 'USER_STOPPED'
    assert saved['data']['checkpoint']['search_attempts'] > 0
    assert saved['data']['pending_detail'] == 'one'
    assert source.fetches == ['one']
    assert [call['kind'] for call in s.model.calls] == [('groups',), ('candidates',)]
    assert saved['cards'] == []
