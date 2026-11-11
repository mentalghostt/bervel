"""Incremental profile refresh against independent sources and model responses."""
from copy import deepcopy
import importlib
import json
from threading import Lock
from time import sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from test_named_radar_execution import Repository, tender


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setattr('bervel.modules.ai_tender_radar.brief_ranking._wait_for_retry',
                        lambda delay, heartbeat: heartbeat())


class ProfileRepository:
    def __init__(self, repository):
        self.repository, self.finished = repository, []

    def upsert_card(self, run_id, key, data):
        previous = self.repository.saved.get(key, {})
        card = dict(previous, **deepcopy(data))
        card['analysis_id'] = previous.get('analysis_id') or data.get('analysis_id') or str(uuid4())
        self.repository.saved[key] = card
        return deepcopy(card)

    def finish_refresh(self, scope, source, job_id, error=None):
        self.finished.append(error)


class Source:
    key = 'independent-profile-source'

    def __init__(self, repository, simulated):
        self.repository, self.simulated = repository, simulated
        self.briefs = [{'Id': 'a', 'OrderName': 'Болт М8'}, {'Id': 'b', 'OrderName': 'Болт М8'}]
        self.details = {key: tender(key) for key in ('a', 'b')}
        self.searches, self.fetches = [], []
        self.limits = [{'LimitGroup': 'search', 'AvailableRequests': 200},
                       {'LimitGroup': 'purchase', 'AvailableRequests': 200}]
        self.error_page, self.error_detail = None, None
        self.total_override = None
        self.stop_after_fetch = False

    def read_limits(self):
        return deepcopy(self.limits)

    def search_page(self, words, page, start, end, *, purchase_statuses):
        checkpoint = self.repository.state['refresh_checkpoint']
        assert checkpoint['search_attempts'] == len(self.searches) + 1
        assert checkpoint['pending_search']['page'] == page
        self.searches.append((deepcopy(words), page, purchase_statuses))
        if page == self.error_page:
            raise CoreError('KONTUR_UNAVAILABLE', 'source unavailable')
        return {'PageNumber': page, 'TotalCount': len(self.briefs) if self.total_override is None else self.total_override,
                'Items': deepcopy(self.briefs[page * 50:(page + 1) * 50])}

    def fetch_tender(self, key, *, search_item):
        card = self.repository.saved[key]
        assert card['detail_request_state'] == 'pending'
        assert self.repository.state['pending_detail'] == key
        self.fetches.append(key)
        if self.error_detail:
            raise CoreError(self.error_detail, 'source request failed')
        return deepcopy(self.details[key])


class Model:
    """Produce schema-valid external decisions; calculation/matching stay real."""
    def __init__(self, repository):
        self.repository = repository
        self.calls, self.ranking_ids, self.fail_rank = [], [], False

    def generate(self, request_id, prompt, **kwargs):
        self.calls.append(request_id)
        if request_id.startswith('card-rating-'):
            output = 83
        elif '-keywords-' in request_id:
            groups = json.loads(prompt.split('\nВХОД\n')[1])['groups']
            output = {'groups': [{'id': group['id'], 'keywords': ['болт', 'метизы']} for group in groups]}
        elif '-rank-' in request_id:
            if self.fail_rank:
                raise CoreError('MODEL_INVALID_OUTPUT', 'invalid ranking')
            data = json.loads(prompt[len(importlib.import_module(
                'bervel.modules.ai_tender_radar.brief_ranking').INSTRUCTIONS):])
            # Every discovered primary card is durable before the first model call.
            assert all(row['id'] in self.repository.saved for row in data['candidates'])
            assert len(data['candidates']) == 1
            assert kwargs.get('output_schema') is None
            self.ranking_ids.append(data['candidates'][0]['id'])
            output = 60 if data['candidates'][0]['id'] == 'b' else 50
        else:
            batch = json.loads(prompt.split('\nВХОД\n', 1)[1])
            output = {'positions': [{'id': pos['id'], 'matches': [
                {'sku': candidate['sku'], 'evidence': [
                    {'requirement_id': req, 'field': next(iter(fields))}
                    for req, fields in candidate['evidence'].items()]}
                for candidate in pos['candidates']]} for pos in batch]}
        return {'model': 'independent-profile-model', 'answer': json.dumps(output)}


@pytest.fixture(params=['test_api', 'kontur_api'])
def refresh(request, monkeypatch):
    # Only the external prose review is replaced; matching/calculation/fulfillment run normally.
    monkeypatch.setattr('bervel.modules.ai_tender_radar.review.review_card',
        lambda card, *args, **kwargs: {'status': 'saved', 'coverage': card['analysis']['coverage_percent']})
    repository = Repository()
    source = Source(repository, request.param == 'test_api')
    profiles = ProfileRepository(repository)
    repository.upsert_card = lambda *args: profiles.upsert_card(*args)
    pipeline = SimpleNamespace(repository=repository, profiles=SimpleNamespace(repository=profiles),
        sources={request.param: source}, model=Model(repository),
        jobs=SimpleNamespace(heartbeat=lambda job_id: None))
    pipeline.ratings = []
    pipeline.rate_run = lambda *args, **kwargs: pipeline.ratings.append(deepcopy(repository.saved))
    run = {'run_id': uuid4(), 'data': {}}
    operation = {'kind': 'profile_refresh', 'profile_source': request.param, 'run_id': str(run['run_id']),
        'refresh_version': 'incremental-profile-v2',
        'settings_snapshot_id': str(uuid4()), 'keyword_mode': 'manual', 'keywords': ['болт', 'метизы'],
        'detail_max': 1, 'search_fingerprint': 'immutable-operation-fingerprint'}
    job = {'job_id': uuid4(), 'actor_id': 'operator', 'data_scope': 'private', 'operation': operation}
    profile = {'products': [{'id': 'bolt', 'sku': 'B8', 'name': 'Болт М8', 'enabled': True}],
        'experimental_products': [], 'include_experimental': False}
    stock = {'data_kind': 'real', 'rows': [{'row': 1, 'sku': 'B8', 'product': 'Болт М8',
        'free_quantity': '10', 'unit': 'шт', 'unit_cost': '5', 'currency': 'RUB',
        'as_of': '2026-11-09T00:00:00+00:00', 'attributes': {}}]}
    return SimpleNamespace(pipeline=pipeline, repository=repository, source=source, profiles=profiles,
        run=run, job=job, profile=profile, stock=stock)


def execute(env):
    module = importlib.import_module('bervel.modules.ai_tender_radar.profile_refresh')
    return module.execute_profile_refresh(env.pipeline, env.job, env.run, env.profile, env.stock)


def next_refresh(env, **changes):
    env.run['data'] = deepcopy(env.repository.state)
    env.job = dict(env.job, job_id=uuid4(), operation=dict(env.job['operation'], **changes))
    env.source.searches.clear()


def legacy_refresh(env):
    """An already-started pre-upgrade refresh retains its historical stages."""
    from bervel.modules.ai_tender_radar.runs import _stage_versions
    env.run['data'].update(stage_versions=_stage_versions('ai-selection-v1'), refresh_checkpoint={
        'job_id': str(env.job['job_id']), 'page': 0, 'seen_ids': [], 'search_attempts': 0,
        'detail_attempts': 0, 'analyzed_ids': [], 'search_start': '1970-01-01T00:00:00Z',
        'search_end': '2026-11-09T00:00:00Z'})


def test_saves_all_briefs_before_ai_and_gets_highest_priority_first(refresh):
    execute(refresh)
    assert set(refresh.repository.saved) == {'a', 'b'}
    assert refresh.source.fetches == ['b']
    assert refresh.source.searches == [(['болт', 'метизы'], 0, [1, 4])]
    assert refresh.repository.saved['a']['information_state'] == 'primary'
    full = refresh.repository.saved['b']
    assert full['information_state'] == 'full' and full['details_loaded']
    assert full['analysis']['coverage_percent'] == '100'
    assert full['analysis_settings_snapshot_id'] == refresh.job['operation']['settings_snapshot_id']
    assert full['priority_score'] == 60
    assert 'rating' not in full  # Brief priority cannot become the final rating.
    assert full['individual_rating']['score'] == 83
    assert refresh.pipeline.ratings == []


def test_full_stage_retry_honors_provider_retry_after_before_later_model_calls(refresh, monkeypatch):
    clock = [1000.0]
    waits = []
    monkeypatch.setattr('bervel.modules.ai_tender_radar.profile_refresh.time', lambda: clock[0])
    def wait(seconds, heartbeat):
        waits.append(seconds)
        clock[0] += seconds
        heartbeat()
    monkeypatch.setattr('bervel.modules.ai_tender_radar.brief_ranking._wait_for_retry', wait)
    original = refresh.pipeline.model.generate
    failures = []
    def generate(request_id, prompt, **kwargs):
        if request_id.startswith('run-') and not failures:
            failures.append(request_id)
            error = CoreError('MODEL_RATE_LIMITED', 'Provider cooldown')
            error.retry_after_seconds = 120
            raise error
        return original(request_id, prompt, **kwargs)
    refresh.pipeline.model.generate = generate
    execute(refresh)
    assert max(waits) >= 120
    assert refresh.repository.state['state'] == 'complete'
    assert refresh.repository.state['refresh_checkpoint']['card_rating_not_before'] == 1120


def test_zero_detail_budget_searches_all_pages_and_retains_primary_cards(refresh):
    refresh.job['operation']['detail_max'] = 0
    refresh.source.briefs = [{'Id': str(index), 'OrderName': 'Болт М8'} for index in range(121)]
    execute(refresh)
    assert len(refresh.repository.saved) == 121
    assert [search[1] for search in refresh.source.searches] == [0, 1, 2]
    assert refresh.source.fetches == []
    assert all(not card['details_loaded'] for card in refresh.repository.saved.values())


def test_second_refresh_preserves_analysis_and_chat_and_fetches_only_missing_details(refresh):
    execute(refresh)
    identifier = refresh.repository.saved['b']['analysis_id']
    refresh.repository.saved['b']['chat'] = [{'question': 'Срок?', 'answer': 'Сохранённый'}]
    next_refresh(refresh)
    execute(refresh)
    assert refresh.source.fetches == ['b', 'a']
    assert refresh.repository.saved['b']['analysis_id'] == identifier
    assert refresh.repository.saved['b']['chat'][0]['answer'] == 'Сохранённый'


def test_existing_details_are_reanalyzed_on_current_stock_and_new_sku(refresh):
    execute(refresh)
    next_refresh(refresh, detail_max=0)
    refresh.stock['rows'][0]['free_quantity'] = '0'
    execute(refresh)
    assert refresh.repository.saved['b']['analysis']['coverage_percent'] == '0'
    refresh.stock['rows'].append(dict(refresh.stock['rows'][0], sku='NEW', free_quantity='10'))
    next_refresh(refresh, detail_max=0)
    execute(refresh)
    full = refresh.repository.saved['b']
    assert full['analysis']['coverage_percent'] == '100'
    assert any(match['sku'] == 'NEW' for match in full['analysis']['positions'][0]['matches'])
    assert refresh.source.fetches == ['b']


def test_ranking_failure_keeps_all_briefs_and_does_not_spend_details(refresh):
    refresh.pipeline.model.fail_rank = True
    execute(refresh)
    assert set(refresh.repository.saved) == {'a', 'b'}
    assert refresh.repository.saved['b']['priority_score'] is None
    assert not refresh.source.fetches
    assert refresh.repository.state['state'] == 'complete'
    assert refresh.repository.state['completion_summary']['primary_failed'] == 2
    assert all(error['code'] == 'MODEL_INVALID_OUTPUT' for error in
               refresh.repository.state['refresh_checkpoint']['completion_errors']['primary'].values())
    assert refresh.profiles.finished[-1] is None


def test_missing_only_after_successful_complete_same_scope(refresh):
    execute(refresh)
    next_refresh(refresh, detail_max=0)
    refresh.source.briefs = refresh.source.briefs[:1]
    execute(refresh)
    assert refresh.repository.saved['b']['source_missing'] is True
    assert refresh.repository.saved['b']['metrics']['actuality'] == 'inactive'
    assert refresh.repository.saved['a']['metrics']['actuality'] == 'active'


def test_changed_keywords_preserve_unseen_old_active_cards(refresh):
    execute(refresh)
    next_refresh(refresh, detail_max=0, keywords=['гайка'])
    refresh.source.briefs = []
    execute(refresh)
    assert refresh.repository.saved['b']['source_missing'] is False
    assert refresh.repository.saved['b']['metrics']['actuality'] == 'active'


@pytest.mark.parametrize('failure', ['quota', 'error', 'invalid', 'duplicate'])
def test_incomplete_traversal_does_not_mark_unseen_cards_missing(refresh, failure):
    execute(refresh)
    next_refresh(refresh, detail_max=0)
    refresh.source.briefs = [{'Id': str(index), 'OrderName': 'Болт М8'} for index in range(51)]
    if failure == 'quota':
        refresh.source.limits[0]['AvailableRequests'] = 1
    elif failure == 'error':
        refresh.source.error_page = 1
    elif failure == 'invalid':
        refresh.source.total_override = 52
    else:
        refresh.source.briefs[-1] = refresh.source.briefs[0]
    if failure in {'error', 'invalid'}:
        with pytest.raises(CoreError):
            execute(refresh)
    else:
        execute(refresh)
    assert refresh.repository.saved['b']['source_missing'] is False


def test_unknown_detail_outcome_is_durable_and_never_automatically_retried(refresh):
    refresh.source.error_detail = 'KONTUR_UNAVAILABLE'
    with pytest.raises(CoreError):
        execute(refresh)
    assert refresh.repository.saved['b']['detail_request_state'] == 'unknown'
    refresh.source.error_detail = None
    next_refresh(refresh)
    execute(refresh)
    assert refresh.source.fetches == ['b', 'a']
    assert refresh.repository.saved['b']['information_state'] == 'primary'


def test_known_404_marks_unavailable_without_deleting_card(refresh):
    refresh.source.error_detail = 'KONTUR_NOT_FOUND'
    execute(refresh)
    assert refresh.repository.saved['b']['inactive_reason'] == 'source_unavailable'
    assert refresh.repository.saved['b']['metrics']['actuality'] == 'inactive'
    assert refresh.repository.saved['a']['information_state'] == 'primary'


def test_unknown_search_outcome_is_not_replayed_on_restart(refresh):
    refresh.source.error_page = 0
    with pytest.raises(CoreError):
        execute(refresh)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.source.error_page = None
    with pytest.raises(CoreError) as error:
        execute(refresh)
    assert error.value.code == 'SOURCE_REQUEST_OUTCOME_UNKNOWN'
    assert len(refresh.source.searches) == 1


def test_completed_job_replay_makes_no_source_or_model_calls(refresh):
    execute(refresh)
    counts = (len(refresh.source.searches), len(refresh.source.fetches), len(refresh.pipeline.model.calls))
    refresh.run['data'] = deepcopy(refresh.repository.state)
    execute(refresh)
    assert counts == (len(refresh.source.searches), len(refresh.source.fetches), len(refresh.pipeline.model.calls))


def test_auto_keywords_cached_by_enabled_products_between_refreshes(refresh):
    refresh.job['operation'].update(keyword_mode='auto', keywords=[], detail_max=0)
    execute(refresh)
    next_refresh(refresh)
    execute(refresh)
    assert len([key for key in refresh.pipeline.model.calls if '-keywords-' in key]) == 1
    refresh.profile['products'][0]['name'] = 'Крепёж М8'
    next_refresh(refresh)
    execute(refresh)
    assert len([key for key in refresh.pipeline.model.calls if '-keywords-' in key]) == 2


def test_fresh_brief_updates_old_full_metadata_without_losing_positions(refresh):
    execute(refresh)
    refresh.source.briefs[1].update(OrderName='Обновлённое название', MaxPrice=500, Currency='RUB',
        ApplicationDeadline='2099-12-31T00:00:00Z', TypeOfTrading=2)
    next_refresh(refresh, detail_max=0)
    execute(refresh)
    card = refresh.repository.saved['b']
    assert card['tender']['title'] == 'Обновлённое название'
    assert card['tender']['positions']
    assert card['metrics']['price'] == '500'
    assert card['metrics']['trade_type'] == 'fz223'


@pytest.mark.parametrize(('total', 'missing'), [(9999, True), (10000, False), (10001, False)])
def test_result_cap_never_creates_false_disappearances(refresh, total, missing, monkeypatch):
    execute(refresh)
    next_refresh(refresh, detail_max=0)
    refresh.source.briefs = [{'Id': str(index), 'OrderName': 'Болт М8'} for index in range(total)]
    # This boundary test exercises all 200 source pages, not 10,000 model
    # decisions and their per-card commits. Numeric priority has its own tests.
    monkeypatch.setattr('bervel.modules.ai_tender_radar.profile_refresh.rank_briefs_with_model',
                        lambda *args, **kwargs: [])
    execute(refresh)
    assert len(refresh.source.searches) == 200
    assert len(refresh.repository.saved) == min(total, 10000) + 2
    assert refresh.repository.saved['b']['source_missing'] is missing


def test_provider_zero_details_does_not_restrict_available_search_budget(refresh):
    refresh.source.limits[0]['AvailableRequests'] = 2
    refresh.source.limits[1]['AvailableRequests'] = 0
    refresh.source.briefs = [{'Id': str(index), 'OrderName': 'Болт М8'} for index in range(51)]
    execute(refresh)
    assert len(refresh.source.searches) == 2
    assert len(refresh.repository.saved) == 51
    assert not refresh.source.fetches


def test_unmetered_simulator_caps_at_200_details_without_affecting_search(refresh):
    if not refresh.source.simulated:
        pytest.skip('Only an unmetered simulator has local fallback quotas')
    refresh.source.limits = []
    refresh.source.briefs = [{'Id': str(index), 'OrderName': 'Болт М8'} for index in range(201)]
    refresh.source.details = {str(index): tender(str(index)) for index in range(201)}
    refresh.job['operation']['detail_max'] = None
    execute(refresh)
    assert len(refresh.source.searches) == 5
    assert len(refresh.source.fetches) == 200
    assert len(refresh.repository.saved) == 201


def test_stop_after_http_retains_paid_detail_for_restart(refresh):
    stopped = False
    fetch = refresh.source.fetch_tender

    def stop_after_fetch(*args, **kwargs):
        nonlocal stopped
        value = fetch(*args, **kwargs)
        stopped = True
        return value

    def heartbeat(job_id):
        if stopped:
            raise CoreError('USER_STOPPED', 'stopped')

    refresh.source.fetch_tender = stop_after_fetch
    refresh.pipeline.jobs.heartbeat = heartbeat
    with pytest.raises(CoreError) as error:
        execute(refresh)
    assert error.value.code == 'USER_STOPPED'
    assert refresh.repository.saved['b']['details_loaded']
    assert refresh.repository.saved['b']['raw_tender']['positions']
    refresh.pipeline.jobs.heartbeat = lambda job_id: None
    refresh.source.fetch_tender = fetch
    refresh.run['data'] = deepcopy(refresh.repository.state)
    execute(refresh)
    assert refresh.source.fetches == ['b']
    assert refresh.repository.saved['b']['analysis']['coverage_percent'] == '100'


def test_analysis_model_failure_preserves_existing_verified_result_and_exposes_error(refresh):
    execute(refresh)
    previous = deepcopy(refresh.repository.saved['b']['analysis'])
    next_refresh(refresh, detail_max=0)
    refresh.stock['rows'][0]['product'] = 'Болт М8 новая поставка'
    generate = refresh.pipeline.model.generate

    def failing_matching(request_id, *args, **kwargs):
        if request_id.startswith('run-'):
            raise CoreError('MODEL_UNAVAILABLE', 'model unavailable')
        return generate(request_id, *args, **kwargs)

    refresh.pipeline.model.generate = failing_matching
    execute(refresh)
    assert refresh.repository.saved['b']['analysis'] == previous
    assert refresh.repository.saved['b']['analysis_stale'] is True
    assert refresh.repository.saved['b']['analysis_refresh_error']['code'] == 'MODEL_UNAVAILABLE'
    assert refresh.repository.state['state'] == 'complete'
    assert refresh.repository.state['completion_summary']['analysis_failed'] == 1


def test_failed_comparative_rating_is_not_reported_as_successful_refresh(refresh):
    legacy_refresh(refresh)
    refresh.pipeline.rate_run = lambda *args, **kwargs: {
        'status': 'unknown', 'error': {'code': 'RATING_INTERRUPTED', 'message': 'interrupted'}}
    with pytest.raises(CoreError) as error:
        execute(refresh)
    assert error.value.code == 'RATING_INTERRUPTED'
    assert refresh.repository.saved['b']['analysis']
    assert refresh.repository.state['state'] == 'failed'


def test_stopped_final_save_does_not_turn_failed_refresh_into_completed_replay(refresh):
    legacy_refresh(refresh)
    def rating(*args, **kwargs):
        refresh.pipeline.jobs.heartbeat = lambda job_id: (_ for _ in ()).throw(CoreError('USER_STOPPED', 'stopped'))
        return {'status': 'complete'}

    refresh.pipeline.rate_run = rating
    with pytest.raises(CoreError):
        execute(refresh)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.pipeline.jobs.heartbeat = lambda job_id: None
    refresh.pipeline.rate_run = lambda *args, **kwargs: {'status': 'complete'}
    execute(refresh)
    assert refresh.repository.state['state'] == 'complete'


def test_stop_after_last_search_response_does_not_mark_old_cards_missing(refresh):
    execute(refresh)
    next_refresh(refresh, detail_max=0)
    refresh.source.briefs = []
    search = refresh.source.search_page

    def stop_after_search(*args, **kwargs):
        value = search(*args, **kwargs)
        refresh.pipeline.jobs.heartbeat = lambda job_id: (_ for _ in ()).throw(CoreError('USER_STOPPED', 'stopped'))
        return value

    refresh.source.search_page = stop_after_search
    with pytest.raises(CoreError):
        execute(refresh)
    assert refresh.repository.saved['b']['source_missing'] is False


def test_expired_planned_brief_stays_visible_and_does_not_spend_details(refresh):
    refresh.source.briefs[1].update(IsPlanning=True, ApplicationDeadline='2000-01-01T00:00:00Z')
    execute(refresh)
    assert set(refresh.repository.saved) == {'a', 'b'}
    assert refresh.source.fetches == ['a']
    assert refresh.repository.saved['b']['metrics']['inactivity_reason'] == 'deadline_expired'


def test_saved_source_with_incomplete_details_is_not_paid_for_again(refresh):
    refresh.source.details['b']['positions'] = []
    execute(refresh)
    next_refresh(refresh)
    execute(refresh)
    assert refresh.source.fetches == ['b', 'a']
    assert refresh.repository.saved['b']['details_loaded'] is True
    assert refresh.repository.saved['b']['information_state'] == 'full'


def test_durable_pending_detail_from_process_death_is_not_replayed(refresh):
    execute(refresh)
    refresh.repository.saved['a']['detail_request_state'] = 'pending'
    next_refresh(refresh)
    execute(refresh)
    assert refresh.source.fetches == ['b']
    assert refresh.repository.saved['a']['detail_request_state'] == 'unknown'


def test_duplicate_priority_scores_use_stable_identifier_order(refresh):
    refresh.source.briefs = [{'Id': key, 'OrderName': 'Болт М8'} for key in ('z', 'c', 'a')]
    refresh.source.details = {key: tender(key) for key in ('z', 'c', 'a')}
    execute(refresh)
    assert refresh.source.fetches == ['a']


def test_provider_detail_budget_limits_spending_separately_from_search(refresh):
    refresh.source.limits[1]['AvailableRequests'] = 1
    refresh.job['operation']['detail_max'] = 100
    execute(refresh)
    assert refresh.source.fetches == ['b']
    assert refresh.repository.state['effective_budgets'] == {'search': 200, 'detail': 1}
    assert refresh.repository.state['quotas'] == {'search': 199, 'detail': 0}


def test_cancelled_during_details_is_closed_rather_than_missing(refresh):
    refresh.source.error_detail = 'KONTUR_TENDER_CLOSED'
    execute(refresh)
    card = refresh.repository.saved['b']
    assert card['metrics']['inactivity_reason'] == 'stage_closed'
    assert card['details_loaded'] is False


def test_analysis_fans_out_to_at_most_ten_cards(refresh):
    refresh.source.briefs = [{'Id': str(index), 'OrderName': 'Болт М8'} for index in range(10)]
    refresh.source.details = {str(index): tender(str(index)) for index in range(10)}
    refresh.job['operation']['detail_max'] = 10
    generate = refresh.pipeline.model.generate
    lock = Lock()
    active, maximum = 0, 0

    def measured(*args, **kwargs):
        nonlocal active, maximum
        if '-rank-' in args[0]:
            return generate(*args, **kwargs)
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            sleep(.01)
            return generate(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    refresh.pipeline.model.generate = measured
    execute(refresh)
    assert 1 < maximum <= 10
    assert all(card['analysis']['coverage_percent'] == '100' for card in refresh.repository.saved.values())


def test_inactive_existing_full_cards_also_recalculate_on_current_stock(refresh):
    execute(refresh)
    refresh.source.briefs[1]['ApplicationDeadline'] = '2000-01-01T00:00:00Z'
    refresh.stock['rows'][0]['free_quantity'] = '0'
    next_refresh(refresh, detail_max=0)
    execute(refresh)
    card = refresh.repository.saved['b']
    assert card['metrics']['actuality'] == 'inactive'
    from bervel.modules.ai_tender_radar.profile_policy import profile_metrics
    assert profile_metrics(card)['stock_coverage_percent'] == '0'
    assert card['analysis']['coverage_percent'] == '100'  # The frozen analysis is preserved.
    assert refresh.source.fetches == ['b']
