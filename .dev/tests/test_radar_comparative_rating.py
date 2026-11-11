"""Comparative rating contracts; the model is the only substituted boundary."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json
from uuid import UUID, uuid4

import pytest

from bervel.core.errors import CoreError
from test_ai_radar_runs import run_scenario, completed, API, _deny_source_access


def implementation():
    name = 'bervel.modules.ai_tender_radar.comparative_rating'
    assert importlib.util.find_spec(name) is not None, 'The compact comparative rating scenario is missing'
    return importlib.import_module(name)


def card(index, *, coverage='5.75426572428071678446491', margin='77.113392956751329441', product=None):
    return {'analysis_id': str(UUID(int=index + 1)), 'state': 'failed' if index % 2 else 'needs_data',
        'tender': {'title': 'PRIVATE TITLE', 'positions': [{'sku': 'PRIVATE-SKU'}]},
        'analysis': {'positions': [{'secret': 'PRIVATE REQUIREMENTS'}]},
        'fulfillment': {'status': 'infeasible', 'reasons': ['CAPACITY_INSUFFICIENT']},
        'assessment': {'score': 0}, 'opportunity': {'score': 0, 'basis': 'confirmed', 'margin_percent': margin},
        'metrics': {'price': '540856.774', 'currency': 'RUB', 'catalog_match_percent': product,
            'catalog_match_incomplete': True, 'stock_coverage_percent': coverage,
            'stock_coverage_incomplete': False}}


class Model:
    def __init__(self, saved, *, error=None, transform=None):
        self.saved, self.error, self.transform, self.calls = saved, error, transform, []

    def generate(self, request_id, prompt, **options):
        assert self.saved[-1]['status'] == 'running'
        assert self.saved[-1]['pending_request_id'] == request_id
        assert options['web_search'] is False
        payload = json.loads(prompt.split('\nВХОД\n', 1)[1])
        self.calls.append(payload)
        if self.error:
            raise self.error
        ratings = [{'id': row['id'], 'score': 92 if row['stock_coverage']['percent'] else 50}
                   for row in payload['cards']]
        if self.transform:
            ratings = self.transform(ratings)
        return {'model': 'controlled-rating-model', 'answer': json.dumps({'ratings': ratings})}


def evaluate(cards, **model_options):
    module = implementation()
    saved = []
    model = Model(saved, **model_options)
    result = module.rate_snapshot(module.build_snapshot(cards), model, 'rating-test',
        heartbeat=lambda: None, save=lambda value: saved.append(deepcopy(value)))
    return module, result, model, saved


def test_100_cards_use_one_compact_call_only_four_aggregates_and_keep_unknowns():
    cards = [card(index, coverage=None if index % 2 else '5.75426572428071678446491') for index in range(100)]
    original = deepcopy(cards)
    module, result, model, saved = evaluate(cards)
    assert cards == original
    assert result['status'] == 'complete' and result['cohort_size'] == 100
    assert len(model.calls) == 1 and len(result['ratings']) == 100
    assert len({row['id'] for row in result['ratings']}) == 100
    prompt_input = model.calls[0]
    for row in prompt_input['cards']:
        assert set(row) == {'id', 'price', 'product_match', 'stock_coverage', 'margin'}
        assert row['product_match']['percent'] is None
        assert row['margin']['scope'] == 'confirmed'
    first = prompt_input['cards'][0]
    assert first['price']['amount'] == '540856.77'
    assert first['stock_coverage']['percent'] == '5.7543'
    assert first['margin']['percent'] == '77.1134'
    assert 'PRIVATE' not in json.dumps(prompt_input)
    assert 'infeasible' not in json.dumps(prompt_input)
    assert result['ratings'][0]['score'] == 92  # Not multiplied by 5.75% or zeroed by feasibility.
    assert len(json.dumps(prompt_input).encode()) < module.MAX_PROMPT_BYTES
    assert saved[0]['status'] == 'running' and saved[-1]['status'] == 'complete'


def test_fingerprint_is_order_independent_and_tracks_aggregate_scope_and_cohort():
    module = implementation()
    cards = [card(0), card(1)]
    fingerprint = module.build_snapshot(cards)['fingerprint']
    assert module.build_snapshot(list(reversed(cards)))['fingerprint'] == fingerprint
    for mutate in [lambda rows: rows.pop(),
                   lambda rows: rows[0]['opportunity'].update(basis='full'),
                   lambda rows: rows[0]['metrics'].update(stock_coverage_incomplete=True),
                   lambda rows: rows[0]['metrics'].update(stock_coverage_percent='7')]:
        changed = deepcopy(cards)
        mutate(changed)
        assert module.build_snapshot(changed)['fingerprint'] != fingerprint


@pytest.mark.parametrize('transform', [lambda rows: rows[:-1], lambda rows: rows + rows[:1],
    lambda rows: [dict(rows[0], id='foreign'), *rows[1:]],
    *[lambda rows, score=score: [dict(rows[0], score=score), *rows[1:]] for score in [True, 1.5, -1, 101, '80']]])
def test_unverifiable_model_scores_fail_without_fabricating_missing_results(transform):
    _, result, _, saved = evaluate([card(0), card(1)], transform=transform)
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'
    assert result['ratings'] == [] and saved[-1]['status'] == 'failed'


def test_timeout_is_unknown_and_successful_batches_are_checkpointed(monkeypatch):
    module = implementation()
    monkeypatch.setattr(module, 'MAX_BATCH', 2)
    saved = []
    model = Model(saved)
    generate = model.generate
    def interrupted(*args, **kwargs):
        if len(model.calls) == 1:
            raise CoreError('MODEL_TIMEOUT', 'Unknown remote outcome')
        return generate(*args, **kwargs)
    model.generate = interrupted
    result = module.rate_snapshot(module.build_snapshot([card(i) for i in range(5)]), model, 'unknown-test',
        heartbeat=lambda: None, save=lambda value: saved.append(deepcopy(value)))
    assert result['status'] == 'unknown'
    assert len(result['ratings']) == 2 and len(result['completed_requests']) == 1
    assert result['pending_request_id'] is not None
    assert len(saved[-2]['ratings']) == 2


def test_large_cohort_is_bounded_without_losing_ids_and_uses_global_percentiles():
    module = implementation()
    cards = [card(i, coverage=str(i / 1000)) for i in range(1207)]
    snapshot = module.build_snapshot(cards)
    batches = module.build_batches(snapshot)
    assert len(batches) > 1
    assert sum(len(batch['cards']) for batch in batches) == 1207
    assert len({row['id'] for batch in batches for row in batch['cards']}) == 1207
    assert all(batch['cohort'] == batches[0]['cohort'] for batch in batches)
    assert all(len((module.INSTRUCTIONS + module.json_text(batch)).encode()) <= module.MAX_PROMPT_BYTES for batch in batches)
    assert batches[0]['cohort']['size'] == 1207
    assert batches[0]['cards'][0]['stock_coverage']['percentile'] == '0'
    assert batches[-1]['cards'][-1]['stock_coverage']['percentile'] == '100'


def test_byte_boundary_splits_without_recomputing_the_comparative_basis(monkeypatch):
    module = implementation()
    monkeypatch.setattr(module, 'MAX_PROMPT_BYTES', 6000)
    batches = module.build_batches(module.build_snapshot([card(i, product=str(i)) for i in range(100)]))
    assert len(batches) > 1
    assert sum(len(batch['cards']) for batch in batches) == 100
    assert all(len((module.INSTRUCTIONS + module.json_text(batch)).encode()) <= 6000 for batch in batches)
    assert all(batch['cohort'] == batches[0]['cohort'] for batch in batches)


def test_projection_is_read_only_and_failed_unknown_stale_are_never_zero():
    module = implementation()
    cards = [card(0), card(1)]
    original = deepcopy(cards)
    _, result, _, _ = evaluate(cards)
    reports = [{'job_id': str(uuid4()), 'data': {'kind': 'rating', 'result': result}}]
    module.apply_rating(cards, reports)
    assert cards[0]['rating']['score'] == 92
    assert cards[0]['opportunity'] == original[0]['opportunity']
    assert cards[0]['fulfillment'] == original[0]['fulfillment']
    for status in ['failed', 'unknown', 'running']:
        reports[0]['data']['result'] = dict(result, status=status)
        module.apply_rating(cards, reports)
        assert cards[0]['rating']['score'] is None
    reports[0]['data']['result'] = result
    cards[0]['metrics']['price'] = '100'
    module.apply_rating(cards, reports)
    assert cards[0]['rating'] == {'status': 'stale', 'score': None}


def test_interrupted_worker_cannot_certify_or_repeat_a_pending_request():
    module = implementation()
    cards = [card(0)]
    snapshot = module.build_snapshot(cards)
    saved = []
    model = Model(saved)
    def process_crash(*args, **kwargs):
        raise KeyboardInterrupt
    model.generate = process_crash
    with pytest.raises(KeyboardInterrupt):
        module.rate_snapshot(snapshot, model, 'crashed-worker', heartbeat=lambda: None,
            save=lambda value: saved.append(deepcopy(value)))
    assert saved[-1]['status'] == 'running'
    assert saved[-1]['pending_request_id'] == 'crashed-worker-rating-0'
    reports = [{'job_id': str(uuid4()), 'job_state': 'failed', 'data': {'kind': 'rating', 'result': saved[-1]}}]
    assert module.matching_report(snapshot, reports) == reports[0]
    module.apply_rating(cards, reports)
    assert cards[0]['rating'] == {'status': 'unknown', 'score': None}


def test_repository_reserves_same_fingerprint_once_and_terminal_report_is_immutable(pg_engine):
    module = implementation()
    from bervel.modules.ai_tender_radar.infrastructure.runs import PostgresRunRepository
    repository = PostgresRunRepository(pg_engine)
    run_id, job_id, snapshot_id = uuid4(), uuid4(), uuid4()
    repository.ensure('private', run_id, snapshot_id, {'state': 'complete'})
    result = dict(module.build_snapshot([card(0)]), status='running', ratings=[])
    result.pop('cards')
    first = repository.reserve_rating(job_id, run_id, result)
    second = repository.reserve_rating(uuid4(), run_id, result)
    assert first['job_id'] == second['job_id'] == job_id
    repository.report(job_id, run_id, {'kind': 'rating', 'result': dict(result, status='unknown')})
    repository.report(job_id, run_id, {'kind': 'rating', 'result': dict(result, status='complete')})
    assert repository.read_reports(run_id)[0]['data']['result']['status'] == 'unknown'


def test_two_racing_rating_reservations_share_one_durable_attempt(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from bervel.modules.ai_tender_radar.infrastructure.runs import PostgresRunRepository
    module = implementation()
    repository = PostgresRunRepository(pg_engine)
    run_id = uuid4()
    repository.ensure('private', run_id, uuid4(), {'state': 'complete'})
    result = module.initial_result(module.build_snapshot([card(0)]), 'race')
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(repository.reserve_rating, uuid4(), run_id, result) for _ in range(2)]
        first, second = [future.result() for future in futures]
    assert first['job_id'] == second['job_id']
    assert sum(entry['created'] for entry in [first, second]) == 1
    assert len(repository.read_reports(run_id)) == 1


def test_existing_100_metrics_with_unknown_match_are_scored_without_repeating_review():
    _, result, _, _ = evaluate([card(i, coverage=None if i >= 50 else str((i + 1) / 10)) for i in range(100)])
    assert len(result['ratings']) == 100 and result['status'] == 'complete'


def install_rating_model(scenario, monkeypatch, *, error=None):
    original = scenario.model.generate
    calls = []
    def generate(request_id, prompt, **options):
        if 'ratings' not in (options.get('output_schema') or {}).get('properties', {}):
            return original(request_id, prompt, **options)
        payload = json.loads(prompt.split('\nВХОД\n', 1)[1])
        calls.append(payload)
        from bervel.modules.ai_tender_radar.infrastructure.runs import reports
        from sqlalchemy import select
        with scenario.engine.connect() as conn:
            pending = conn.execute(select(reports.c.data).where(reports.c.data['kind'].astext == 'rating')).scalars().all()
        assert any(row['result']['status'] == 'running' and row['result']['pending_request_id'] == request_id for row in pending)
        if error:
            raise error
        return {'model': 'controlled-rating-model', 'answer': json.dumps({'ratings': [
            {'id': row['id'], 'score': 91} for row in payload['cards']]})}
    monkeypatch.setattr(scenario.model, 'generate', generate)
    return calls


@pytest.mark.parametrize('error', [None, CoreError('MODEL_TIMEOUT', 'Unknown remote outcome')])
def test_manual_operation_read_replay_and_reexecution_never_repeat_rating(run_scenario, monkeypatch, error):
    s = run_scenario
    calls = install_rating_model(s, monkeypatch, error=error)
    receipt, before = completed(s)
    url = API + '/' + receipt['run_id'] + '/rating'
    accepted = s.client.post(url, json={'command_key': 'rating-once'})
    assert accepted.status_code == 202, accepted.text
    assert s.service.run_once()
    after = s.client.get(API + '/' + receipt['run_id']).json()
    status = 'complete' if error is None else 'unknown'
    assert all(c['rating']['status'] == status for c in after['cards'])
    assert all(c['rating']['score'] == (91 if error is None else None) for c in after['cards'])
    for old, new in zip(before['cards'], after['cards']):
        assert all(old[key] == new[key] for key in ('tender', 'analysis', 'assessment', 'opportunity', 'review', 'report_summary'))
    assert len(calls) == 1
    assert s.client.post(url, json={'command_key': 'rating-once'}).json()['job_id'] == accepted.json()['job_id']
    reused = s.client.post(url, json={'command_key': 'same-cohort-new-key'})
    assert reused.status_code == 202, reused.text
    assert reused.json()['job_id'] != accepted.json()['job_id']
    assert s.service.run_once()
    replayed = s.client.get(API + '/' + receipt['run_id']).json()
    assert any(report['job_id'] == reused.json()['job_id'] and report['kind'] == 'rating'
               and report['result']['status'] == status for report in replayed['reports'])
    for _ in range(2):
        assert s.client.get(API + '/' + receipt['run_id']).status_code == 200
    job = {'job_id': UUID(accepted.json()['job_id']), 'actor_id': s.actor.actor_id, 'data_scope': s.actor.data_scope,
        'operation': {'kind': 'rating', 'run_id': receipt['run_id'], 'settings_snapshot_id': before['settings_snapshot_id']}}
    with pytest.raises(CoreError, match='AI_RADAR_JOB_INTERRUPTED'):
        s.service.run_pipeline.execute(job)
    assert len(calls) == 1 and s.service.run_once() is False


def test_named_run_automatically_rates_without_corrupting_successful_details(run_scenario, monkeypatch):
    s = run_scenario
    calls = install_rating_model(s, monkeypatch, error=CoreError('MODEL_TIMEOUT', 'Unknown remote outcome'))
    accepted = s.client.post(API, json={'command_key': 'auto-rating', 'name': 'Named rating', 'source': 'test_api',
        'settings_snapshot_id': s.snapshot['id'], 'detail_max': 1})
    assert accepted.status_code == 202, accepted.text
    assert s.service.run_once()
    after = s.client.get(API + '/' + accepted.json()['run_id']).json()
    assert after['data']['state'] == 'complete'
    assert len(after['cards']) == 1 and after['cards'][0]['analysis'] is not None
    assert after['cards'][0]['rating']['status'] == 'unknown'
    assert len(calls) == 1


@pytest.mark.parametrize('error', [None, CoreError('MODEL_TIMEOUT', 'Unknown remote outcome')])
def test_reused_fingerprint_keeps_second_command_receipt_after_cohort_changes(run_scenario, monkeypatch, error):
    s = run_scenario
    calls = install_rating_model(s, monkeypatch, error=error)
    receipt, before = completed(s)
    run_id = UUID(receipt['run_id'])
    url = API + '/' + receipt['run_id'] + '/rating'
    first = s.client.post(url, json={'command_key': 'original-rating'}).json()
    assert s.service.run_once()
    second = s.client.post(url, json={'command_key': 'reused-rating'}).json()
    if second['job_id'] != first['job_id']:
        assert s.service.run_once()  # Reuse can have its own receipt, but cannot call the model again.
    assert len(calls) == 1
    s.service.run_pipeline.repository.add_card(run_id, 'later-card', {'analysis_id': str(uuid4()),
        'candidate_id': 'later-card', 'state': 'failed', 'evaluated_at': datetime.now(timezone.utc).isoformat(),
        'tender': None, 'analysis': None, 'fulfillment': None, 'assessment': None, 'review': None})
    changed = s.client.get(API + '/' + receipt['run_id']).json()
    assert all(card['rating']['status'] == 'stale' for card in changed['cards'])
    repeated = s.client.post(url, json={'command_key': 'reused-rating'})
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()['job_id'] == second['job_id']
    conflicting = s.client.post(API + '/' + receipt['run_id'] + '/question', json={
        'command_key': 'reused-rating', 'analysis_id': before['cards'][0]['analysis_id'], 'question': 'Проверить условия'})
    assert conflicting.status_code == 409, conflicting.text
    assert conflicting.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    assert s.service.run_once() is False
    assert len(calls) == 1


@pytest.mark.parametrize('error', [None, CoreError('MODEL_TIMEOUT', 'Unknown remote outcome')])
def test_saved_card_child_automatically_rates_once_with_preserved_budget_and_history(run_scenario, monkeypatch, error):
    s = run_scenario
    parent_receipt, parent = completed(s)
    repository = s.service.run_pipeline.repository
    saved_parent = deepcopy(repository.cards(UUID(parent_receipt['run_id'])))
    remaining = deepcopy(s.source.remaining)
    preceding_calls = len(s.model.calls)
    calls = install_rating_model(s, monkeypatch, error=error)
    _deny_source_access(s, monkeypatch)
    body = {'command_key': 'saved-child-auto-rating', 'analysis_id': parent['cards'][0]['analysis_id']}
    url = API + '/' + parent_receipt['run_id'] + '/reanalyze'
    accepted = s.client.post(url, json=body)
    assert accepted.status_code == 202, accepted.text
    assert s.client.post(url, json=body).json()['job_id'] == accepted.json()['job_id']
    assert s.service.run_once()
    child = s.client.get(API + '/' + accepted.json()['run_id']).json()
    assert child['data']['state'] == 'complete' and child['data']['stop_reason'] == 'saved_card'
    assert len(child['cards']) == 1 and child['cards'][0]['analysis'] is not None
    assert child['cards'][0]['rating'] == {'status': 'complete' if error is None else 'unknown',
                                         'score': 91 if error is None else None}
    assert len(calls) == 1 and len(calls[0]['cards']) == 1
    assert calls[0]['cards'][0]['id'] == child['cards'][0]['analysis_id']
    assert child['data']['budgets']['model'] == 300
    assert child['data']['model_attempts'] == len(s.model.calls) - preceding_calls + len(calls)
    assert child['data']['model_attempts'] <= child['data']['budgets']['model']
    assert child['data']['checkpoint']['search_attempts'] == 0
    assert child['data'].get('detail_attempts', 0) == 0 and child['search_pages'] == []
    assert s.source.remaining == remaining
    assert repository.cards(UUID(parent_receipt['run_id'])) == saved_parent
    assert s.client.get(API + '/' + parent_receipt['run_id']).json() == parent
    replay = s.client.post(url, json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()['job_id'] == accepted.json()['job_id']
    assert replay.json()['run_id'] == accepted.json()['run_id']
    assert s.service.run_once() is False and len(calls) == 1


@pytest.mark.parametrize('stop_requested', [False, True])
def test_auto_rating_remains_active_in_read_list_history_until_job_finishes(run_scenario, monkeypatch, stop_requested):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from test_ai_radar_runs import enqueue
    s = run_scenario
    error = CoreError('MODEL_TIMEOUT', 'Unknown remote outcome') if stop_requested else None
    calls = install_rating_model(s, monkeypatch, error=error)
    controlled = s.model.generate
    entered, release = Event(), Event()
    def paused_rating(request_id, prompt, **options):
        if 'ratings' in options['output_schema']['properties']:
            entered.set()
            assert release.wait(20), 'The test did not release the controlled rating request'
        return controlled(request_id, prompt, **options)
    monkeypatch.setattr(s.model, 'generate', paused_rating)
    receipt = enqueue(s)
    run_id = UUID(receipt['run_id'])
    path = API + '/' + str(run_id)
    repository = s.service.run_pipeline.repository
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(s.service.run_once, lane='analysis')
        try:
            assert entered.wait(10), 'The automatic rating did not start'
            durable = deepcopy(repository.read(s.actor.data_scope, run_id)['data'])
            saved_cards = deepcopy(repository.cards(run_id))
            assert durable['state'] == durable['phase'] == 'complete'
            assert s.service.jobs.read(s.actor.data_scope, run_id)['state'] == 'running'
            for _ in range(2):  # A fresh reader/reload must see the same active owner.
                current = s.client.get(path).json()
                listed = next(row for row in s.client.get(API).json()['runs'] if row['run_id'] == str(run_id))
                history = next(row for row in s.client.get(API + '/history').json()['entries'] if row['id'] == str(run_id))
                assert current['data']['state'] == listed['data']['state'] == history['state'] == 'running'
                assert current['data']['phase'] == listed['data']['phase'] == 'rating'
                assert current['data']['checkpoint'] == durable['checkpoint']
            assert repository.read(s.actor.data_scope, run_id)['data'] == durable
            question = s.client.post(path + '/question', json={'command_key': 'question-during-auto-rating',
                'analysis_id': current['cards'][0]['analysis_id'], 'question': 'Почему такой результат?'})
            assert question.status_code == 202, question.text
            assert pool.submit(s.service.run_once, lane='question').result(timeout=10)
            answered = s.client.get(path).json()
            [report] = [entry for entry in answered['reports'] if entry['kind'] == 'question'
                        and entry['job_id'] == question.json()['job_id']]
            assert report['result']['state'] == 'complete' and not worker.done()
            assert repository.read(s.actor.data_scope, run_id)['data'] == durable
            assert answered['data']['current_job_id'] == str(run_id)
            assert s.client.delete(path).status_code == 409
            if stop_requested:
                assert s.client.post(path + '/stop').status_code == 204
                stopping = s.client.get(path).json()['data']
                assert stopping['state'] == 'running' and stopping['phase'] == 'rating'
                assert stopping['stop_requested'] is True
                assert s.client.get(API + '/history').json()['entries'][0]['stop_requested'] is True
                assert repository.read(s.actor.data_scope, run_id)['data'] == durable
        finally:
            release.set()
        assert worker.result(timeout=10)
    final = s.client.get(path).json()
    final_list = next(row for row in s.client.get(API).json()['runs'] if row['run_id'] == str(run_id))
    final_history = next(row for row in s.client.get(API + '/history').json()['entries'] if row['id'] == str(run_id))
    expected = 'failed' if stop_requested else 'complete'
    assert final['data']['state'] == final_list['data']['state'] == final_history['state'] == expected
    if stop_requested:
        assert final['data']['error']['code'] == final['data']['stop_reason'] == 'USER_STOPPED'
        assert final_history['stop_reason'] == 'USER_STOPPED'
        assert all(card['rating']['status'] == 'unknown' for card in final['cards'])
    else:
        assert final['data']['phase'] == 'complete'
        assert all(card['rating'] == {'status': 'complete', 'score': 91} for card in final['cards'])
    assert repository.read(s.actor.data_scope, run_id)['data'] == durable
    assert repository.cards(run_id) == saved_cards
    assert len(calls) == 1 and s.service.run_once(lane='analysis') is False


def test_job_status_projection_preserves_terminal_and_unrelated_states():
    from bervel.modules.ai_tender_radar.runs import _run_data_with_job_status
    complete = {'state': 'complete', 'phase': 'complete', 'checkpoint': {'finished': True}}
    for terminal in ['loaded', 'no_match', None]:
        assert _run_data_with_job_status(complete, terminal, None) == complete
    failed = {'state': 'failed', 'phase': 'details', 'checkpoint': {'finished': False}}
    assert _run_data_with_job_status(failed, 'running', None) == failed
    stopped = _run_data_with_job_status(complete, 'failed', {'code': 'USER_STOPPED'})
    assert stopped['state'] == 'failed' and stopped['stop_reason'] == 'USER_STOPPED'
    assert complete == {'state': 'complete', 'phase': 'complete', 'checkpoint': {'finished': True}}
