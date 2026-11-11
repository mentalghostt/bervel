"""Independent completion counterexamples: fake external I/O, real queue and saves."""
from collections import Counter
from copy import deepcopy
import importlib
import json
from threading import Lock, RLock
from time import sleep
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from test_named_radar_execution import tender
from test_radar_profile_refresh import refresh, execute, next_refresh, no_retry_wait


def source_cards(env, count):
    keys = [f'card-{index:03d}' for index in range(count)]
    env.source.briefs = [{'Id': key, 'OrderName': 'Болт М8'} for key in keys]
    env.source.details = {key: tender(key) for key in keys}
    env.job['operation']['detail_max'] = count
    return keys


def failing_analysis(monkeypatch, failures):
    module = importlib.import_module('bervel.modules.ai_tender_radar.profile_refresh')
    original = module._analyze
    calls, active, maximum, lock = Counter(), [0], [0], Lock()
    def analyze(card, *args, **kwargs):
        key = card['candidate_id']
        with lock:
            calls[key] += 1
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
        try:
            # Allow ten lifecycles to overlap; a tail beyond ten must also run.
            sleep(.01)
            if key in failures and (not isinstance(failures, dict) or calls[key] <= failures[key]):
                return dict(card, state='failed', error={'code': 'MODEL_INVALID_OUTPUT',
                    'message': 'Позиция документа отсутствует в подтверждённом составе закупки.'})
            return original(card, *args, **kwargs)
        finally:
            with lock:
                active[0] -= 1
    monkeypatch.setattr(module, '_analyze', analyze)
    return calls, maximum


def rating_fault(monkeypatch, env, failures):
    module = importlib.import_module('bervel.modules.ai_tender_radar.profile_refresh')
    original = module.rate_card
    calls = Counter()
    def rate(snapshot, model, *args, **kwargs):
        key = next(key for key, card in env.repository.saved.items()
                   if card['analysis_id'] == snapshot['analysis_id'])
        class Model:
            def generate(self, *args, **kwargs):
                calls[key] += 1
                if calls[key] <= failures.get(key, 0):
                    raise CoreError('MODEL_INVALID_OUTPUT', 'Оценка должна быть целым числом.')
                return model.generate(*args, **kwargs)
        return original(snapshot, Model(), *args, **kwargs)
    monkeypatch.setattr(module, 'rate_card', rate)
    return calls


def summary(env, **counts):
    expected = dict(version='profile-completion-v1', failed_tenders=0, primary_failed=0,
        detail_failed=0, analysis_failed=0, rating_failed=0, metrics_failed=0, **{})
    expected.update(counts)
    assert env.repository.state['state'] == env.repository.state['phase'] == 'complete'
    assert env.repository.state.get('completion_summary') == expected
    assert env.repository.state['refresh_checkpoint']['finished'] is True
    assert env.profiles.finished[-1] is None


@pytest.mark.parametrize('failed_index', [0, 12])
def test_review_bad_analysis_does_not_block_tail_or_independent_rating_retry(refresh, monkeypatch, failed_index):
    keys = source_cards(refresh, 13)
    bad, scoring = keys[failed_index], keys[1]
    analyses, maximum = failing_analysis(monkeypatch, {bad})
    ratings = rating_fault(monkeypatch, refresh, {scoring: 1})
    execute(refresh)
    summary(refresh, failed_tenders=1, analysis_failed=1)
    assert analyses == Counter({key: 3 if key == bad else 1 for key in keys})
    assert maximum[0] <= 10
    assert ratings == Counter({key: 2 if key == scoring else 1 for key in keys if key != bad})
    assert set(refresh.source.fetches) == set(keys)
    assert refresh.repository.saved[bad].get('individual_rating', {}).get('score') is None
    assert all(refresh.repository.saved[key]['individual_rating']['status'] == 'complete'
               for key in keys if key != bad)


def test_review_all_failed_analyses_settle_honestly_with_three_attempts(refresh, monkeypatch):
    keys = source_cards(refresh, 4)
    analyses, _ = failing_analysis(monkeypatch, set(keys))
    execute(refresh)
    summary(refresh, failed_tenders=4, analysis_failed=4)
    assert analyses == Counter({key: 3 for key in keys})
    assert not any(card.get('analysis_completed_refresh_id') for card in refresh.repository.saved.values())
    assert not any(card.get('individual_rating', {}).get('score') is not None
                   for card in refresh.repository.saved.values())


def test_review_exhausted_individual_rating_is_a_visible_bounded_local_failure(refresh, monkeypatch):
    keys = source_cards(refresh, 3)
    ratings = rating_fault(monkeypatch, refresh, {keys[0]: 99})
    execute(refresh)
    summary(refresh, failed_tenders=1, rating_failed=1)
    assert ratings == Counter({keys[0]: 3, keys[1]: 1, keys[2]: 1})
    failed = refresh.repository.saved[keys[0]]
    assert failed['analysis_completed_refresh_id'] == str(refresh.job['job_id'])
    assert failed['individual_rating']['status'] == 'failed'
    assert failed['individual_rating']['score'] is None


def test_review_successful_analysis_retry_clears_its_warning_even_when_rating_still_fails(refresh, monkeypatch):
    keys = source_cards(refresh, 2)
    analyses, _ = failing_analysis(monkeypatch, {keys[0]: 1})
    ratings = rating_fault(monkeypatch, refresh, {keys[0]: 99})
    execute(refresh)
    summary(refresh, failed_tenders=1, rating_failed=1)
    assert analyses[keys[0]] == 2
    assert ratings[keys[0]] == 3


@pytest.mark.parametrize('reanalyze', [False, True])
def test_review_v3_current_cohort_ignores_historical_errors_outside_it(refresh, monkeypatch, reanalyze):
    execute(refresh)
    refresh.repository.saved['b']['analysis_refresh_error'] = {
        'code': 'MODEL_INVALID_OUTPUT', 'message': 'Historical failure outside the next FALSE cohort'}
    refresh.source.briefs = [{'Id': 'fresh', 'OrderName': 'Болт М8'}]
    refresh.source.details['fresh'] = tender('fresh')
    next_refresh(refresh, detail_max=1, refresh_version='incremental-profile-v3', reanalyze_existing=reanalyze)
    analyses, _ = failing_analysis(monkeypatch, {'b', 'fresh'})
    execute(refresh)
    expected = {'fresh', 'b'} if reanalyze else {'fresh'}
    summary(refresh, failed_tenders=len(expected), analysis_failed=len(expected))
    assert set(analyses) == expected
    assert set(refresh.source.fetches) == {'b', 'fresh'}


def test_review_v2_resume_preserves_377_completed_cards_and_56_200_source_counters(refresh, monkeypatch):
    from bervel.modules.ai_tender_radar.individual_rating import build_card_rating_input
    execute(refresh)
    logical = str(refresh.job['job_id'])
    template = deepcopy(refresh.repository.saved['b'])
    refresh.repository.saved.clear()
    keys = [f'saved-{index:03d}' for index in range(400)]
    protected_fields = ('analysis_id', 'raw_tender', 'tender', 'analysis', 'review',
        'individual_rating', 'evaluated_at', 'analysis_settings_snapshot_id')
    protected = {}
    protected_cards = {}
    for index, key in enumerate(keys):
        card = deepcopy(template)
        card.update(candidate_id=key, analysis_id=str(uuid4()), brief={'Id': key, 'OrderName': 'Болт М8'})
        card['priority_evidence']['id'] = key
        if index >= 377:
            card.pop('analysis_completed_refresh_id', None)
            card.pop('individual_rating_completed_refresh_id', None)
            card['individual_rating'] = None
        else:
            snapshot = build_card_rating_input(card, refresh.profile, refresh.stock)
            card['individual_rating'].update(input=snapshot, fingerprint=snapshot['fingerprint'])
            protected[key] = {field: deepcopy(card.get(field)) for field in protected_fields}
            protected_cards[key] = deepcopy(card)
        refresh.repository.saved[key] = card
    state = deepcopy(refresh.repository.state)
    state.update(state='failed', phase='failed', error={'code': 'MODEL_INVALID_OUTPUT', 'message': 'Previous local failure'})
    checkpoint = state['refresh_checkpoint']
    checkpoint.pop('finished', None)
    checkpoint.update(search_attempts=56, detail_attempts=200, search_done=True,
        seen_ids=keys,
        analyzed_ids=keys[:377], analysis_completed=377, analysis_total=400,
        rating_completed=377, rating_total=400, ranked=True, ranked_ids=keys,
        metrics_done={}, metrics_errors={})
    refresh.run['data'] = state
    refresh.job = dict(refresh.job, job_id=uuid4(), operation=dict(refresh.job['operation'],
        refresh_id=logical, resume_of=logical, priority_retry_cycle=str(uuid4())))
    before_fetches, before_searches = deepcopy(refresh.source.fetches), deepcopy(refresh.source.searches)
    analyses, _ = failing_analysis(monkeypatch, {keys[377]})
    execute(refresh)
    summary(refresh, failed_tenders=1, analysis_failed=1)
    assert set(analyses) == set(keys[377:])
    assert analyses[keys[377]] == 3
    assert all(analyses[key] == 1 for key in keys[378:])
    assert refresh.source.fetches == before_fetches and refresh.source.searches == before_searches
    assert refresh.repository.state['stage_versions']['refresh'] == 'incremental-profile-v2'
    assert refresh.repository.state['refresh_checkpoint']['job_id'] == logical
    assert refresh.repository.state['refresh_checkpoint']['search_attempts'] == 56
    assert refresh.repository.state['refresh_checkpoint']['detail_attempts'] == 200
    for key, before in protected.items():
        assert {field: refresh.repository.saved[key].get(field) for field in protected_fields} == before
        assert refresh.repository.saved[key] == protected_cards[key]


def test_review_crash_after_local_failure_save_resumes_without_refetch_or_reset_budget(refresh, monkeypatch):
    keys = source_cards(refresh, 2)
    analyses, _ = failing_analysis(monkeypatch, {keys[0]})
    original = refresh.profiles.upsert_card
    class PowerLoss(BaseException):
        pass
    interrupted = []
    def save(run_id, key, value):
        result = original(run_id, key, value)
        if key == keys[0] and value.get('state') == 'failed' and not interrupted:
            interrupted.append(key)
            raise PowerLoss()
        return result
    monkeypatch.setattr(refresh.profiles, 'upsert_card', save)
    with pytest.raises(PowerLoss):
        execute(refresh)
    source_calls = deepcopy(refresh.source.fetches), deepcopy(refresh.source.searches)
    monkeypatch.setattr(refresh.profiles, 'upsert_card', original)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    logical = str(refresh.job['job_id'])
    refresh.job = dict(refresh.job, job_id=uuid4(), operation=dict(refresh.job['operation'], refresh_id=logical))
    execute(refresh)
    summary(refresh, failed_tenders=1, analysis_failed=1)
    assert analyses[keys[0]] == 3
    assert (refresh.source.fetches, refresh.source.searches) == source_calls


@pytest.mark.parametrize('all_fail', [False, True])
def test_review_primary_failure_skips_ungraded_card_without_inventing_zero(refresh, all_fail):
    keys = source_cards(refresh, 3)
    original = refresh.pipeline.model.generate
    failures = set(keys) if all_fail else {keys[0]}
    calls = Counter()
    instructions = importlib.import_module('bervel.modules.ai_tender_radar.brief_ranking').INSTRUCTIONS
    def generate(request_id, prompt, **kwargs):
        if request_id.startswith('brief-rank-'):
            key = json.loads(prompt[len(instructions):])['candidates'][0]['id']
            calls[key] += 1
            if key in failures:
                raise CoreError('MODEL_INVALID_OUTPUT', 'Bad scalar priority')
        return original(request_id, prompt, **kwargs)
    refresh.pipeline.model.generate = generate
    execute(refresh)
    summary(refresh, failed_tenders=len(failures), primary_failed=len(failures))
    assert calls == Counter({key: 3 if key in failures else 1 for key in keys})
    assert set(refresh.source.fetches) == set(keys) - failures
    assert all(refresh.repository.saved[key]['priority_score'] is None for key in failures)
    assert all(not refresh.repository.saved[key].get('priority_evidence') for key in failures)


def test_review_metrics_storage_failure_propagates_instead_of_warning(refresh, monkeypatch):
    execute(refresh)
    next_refresh(refresh, detail_max=0)
    refresh.stock['rows'][0]['free_quantity'] = '2'
    original = refresh.repository.upsert_card
    def save(run_id, key, value):
        if set(value) == {'metrics_projection'}:
            raise RuntimeError('independent review: storage unavailable')
        return original(run_id, key, value)
    monkeypatch.setattr(refresh.repository, 'upsert_card', save)
    with pytest.raises(RuntimeError, match='storage unavailable'):
        execute(refresh)
    assert refresh.repository.state['state'] == 'failed'


def test_review_frozen_metrics_snapshot_integrity_error_propagates(refresh):
    execute(refresh)
    card = refresh.repository.saved['b']
    card.pop('analysis_metrics_dependencies', None)
    card['analysis_settings_snapshot_id'] = str(uuid4())
    class Settings:
        def read_snapshot(self, *args):
            raise CoreError('STALE_REVISION', 'Independent review: corrupt frozen snapshot')
    refresh.pipeline.settings = Settings()
    next_refresh(refresh, detail_max=0)
    with pytest.raises(CoreError, match='corrupt frozen snapshot') as raised:
        execute(refresh)
    assert raised.value.code == 'STALE_REVISION'
    assert refresh.repository.state['state'] == 'failed'


@pytest.mark.parametrize('failure_kind', ['runtime', 'core', 'sqlalchemy'])
def test_review_analysis_stage_persistence_failure_remains_global(refresh, monkeypatch, failure_kind):
    from sqlalchemy.exc import OperationalError
    errors = {'runtime': RuntimeError('independent review: stage save failed'),
        'core': CoreError('DATABASE_UNAVAILABLE', 'Independent review: stage save failed'),
        'sqlalchemy': OperationalError('SELECT', {}, RuntimeError('stage save failed'))}
    error = errors[failure_kind]
    original = refresh.repository.upsert_card
    def save(run_id, key, value):
        if 'analysis_stage_cache' in value:
            raise error
        return original(run_id, key, value)
    monkeypatch.setattr(refresh.repository, 'upsert_card', save)
    with pytest.raises(type(error)):
        execute(refresh)
    assert refresh.repository.state['state'] == 'failed'


def test_review_completed_rating_save_failure_is_never_unknown_or_retried(refresh, monkeypatch):
    original = refresh.profiles.upsert_card
    saves = []
    def save(run_id, key, value):
        rating = value.get('individual_rating') or {}
        if rating.get('status') == 'complete':
            saves.append(key)
            raise RuntimeError('independent review: rating save failed')
        return original(run_id, key, value)
    monkeypatch.setattr(refresh.profiles, 'upsert_card', save)
    with pytest.raises(RuntimeError, match='rating save failed'):
        execute(refresh)
    assert saves == ['b']
    assert refresh.repository.saved['b']['analysis_completed_refresh_id'] == str(refresh.job['job_id'])
    assert refresh.repository.saved['b']['individual_rating']['status'] == 'running'


def test_review_rating_cooldown_checkpoint_storage_failure_is_global(refresh, monkeypatch):
    original_model, original_update = refresh.pipeline.model.generate, refresh.repository.update
    failed_model, failed_save = [], []
    def generate(request_id, prompt, **kwargs):
        if request_id.startswith('card-rating-') and not failed_model:
            failed_model.append(request_id)
            error = CoreError('MODEL_RATE_LIMITED', 'Independent review: provider cooldown')
            error.retry_after_seconds = .001
            raise error
        return original_model(request_id, prompt, **kwargs)
    def update(scope, run_id, state):
        if state['refresh_checkpoint'].get('card_rating_not_before', 0) > 0 and not failed_save:
            failed_save.append(True)
            raise RuntimeError('independent review: cooldown save failed')
        return original_update(scope, run_id, state)
    monkeypatch.setattr(refresh.pipeline.model, 'generate', generate)
    monkeypatch.setattr(refresh.repository, 'update', update)
    with pytest.raises((CoreError, RuntimeError)) as raised:
        execute(refresh)
    if isinstance(raised.value, CoreError):
        assert raised.value.code == 'PERSISTENCE_ERROR'
    assert failed_save
    assert refresh.repository.state['state'] == 'failed'


def test_review_unknown_paid_rating_outcome_is_saved_once_without_automatic_replay(refresh):
    original = refresh.pipeline.model.generate
    calls = []
    def generate(request_id, prompt, **kwargs):
        if request_id.startswith('card-rating-'):
            calls.append(request_id)
            raise RuntimeError('Independent review: response disconnected after request')
        return original(request_id, prompt, **kwargs)
    refresh.pipeline.model.generate = generate
    execute(refresh)
    summary(refresh, failed_tenders=1, rating_failed=1)
    assert len(calls) == 1
    assert refresh.repository.saved['b']['individual_rating']['status'] == 'unknown'
    assert refresh.repository.saved['b']['individual_rating']['score'] is None
    source_calls = deepcopy(refresh.source.fetches), deepcopy(refresh.source.searches)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    logical = str(refresh.job['job_id'])
    refresh.job = dict(refresh.job, job_id=uuid4(), operation=dict(refresh.job['operation'], refresh_id=logical))
    execute(refresh)
    assert len(calls) == 1
    assert (refresh.source.fetches, refresh.source.searches) == source_calls


def test_review_parallel_stage_and_attempt_maps_use_the_same_snapshot_lock(refresh, monkeypatch):
    """Detect unlocked writes deterministically while peer lifecycles are active."""
    keys = source_cards(refresh, 13)
    execute(refresh)
    next_refresh(refresh, detail_max=0)
    refresh.stock['rows'][0]['free_quantity'] = '2'
    shared_lock, active = RLock(), [0]
    class GuardedMap(dict):
        def __init__(self, values=()):
            for key, value in dict(values).items():
                dict.__setitem__(self, key, GuardedMap(value) if isinstance(value, dict) else value)
        def __deepcopy__(self, memo):
            result = GuardedMap()
            memo[id(self)] = result
            for key, value in self.items():
                dict.__setitem__(result, deepcopy(key, memo), deepcopy(value, memo))
            return result
        def check(self):
            assert not active[0] or shared_lock._is_owned(), 'Shared stage/attempt map changed outside snapshot lock'
        def __setitem__(self, key, value):
            self.check()
            dict.__setitem__(self, key, GuardedMap(value) if isinstance(value, dict) else value)
        def pop(self, *args):
            self.check()
            return dict.pop(self, *args)
    logical = str(refresh.job['job_id'])
    state = refresh.run['data']
    state.update(state='failed', phase='failed')
    checkpoint = state['refresh_checkpoint']
    checkpoint.pop('finished', None)
    checkpoint.update(job_id=logical, analyzed_ids=[], analysis_retry={
        'cycle_id': logical, 'attempts': GuardedMap()}, completion_errors=GuardedMap({
        stage: {} for stage in ('primary', 'detail', 'analysis', 'rating', 'metrics')}))
    module = importlib.import_module('bervel.modules.ai_tender_radar.profile_refresh')
    original = module._analyze
    def analyze(card, *args, **kwargs):
        active[0] += 1
        try:
            sleep(.02 if card['candidate_id'] == keys[0] else .1)
            if card['candidate_id'] == keys[0]:
                return dict(card, state='failed', error={'code': 'MODEL_INVALID_OUTPUT', 'message': 'Local first-card failure'})
            return original(card, *args, **kwargs)
        finally:
            active[0] -= 1
    monkeypatch.setattr(module, 'RLock', lambda: shared_lock)
    monkeypatch.setattr(module, '_analyze', analyze)
    execute(refresh)
    summary(refresh, failed_tenders=1, analysis_failed=1)


def test_review_primary_errors_do_not_mutate_shared_checkpoint_outside_lock(refresh, monkeypatch):
    keys = source_cards(refresh, 13)
    refresh.job['operation']['detail_max'] = 0
    execute(refresh)
    next_refresh(refresh, detail_max=0)
    logical = str(refresh.job['job_id'])
    shared_lock, active, tracked = RLock(), [0], [None]
    class LockedErrors(dict):
        def __deepcopy__(self, memo):
            return LockedErrors(deepcopy(dict(self), memo))
        def check(self):
            assert self is not tracked[0] or not active[0] or shared_lock._is_owned(), \
                'Primary failure map changed outside snapshot lock while peer models were active'
        def __setitem__(self, key, value):
            self.check()
            dict.__setitem__(self, key, value)
        def pop(self, *args):
            self.check()
            return dict.pop(self, *args)
        def setdefault(self, *args):
            self.check()
            return dict.setdefault(self, *args)
    state = refresh.run['data']
    state.update(state='failed', phase='failed')
    checkpoint = state['refresh_checkpoint']
    checkpoint.pop('finished', None)
    checkpoint.update(job_id=logical, ranked=False, ranking_candidate_ids=keys,
        ranking_retry={'cycle_id': logical, 'attempts': {}},
        completion_errors={'primary': LockedErrors()})
    for card in refresh.repository.saved.values():
        card.pop('priority_evidence', None)
        card['priority_score'] = None
    original_update, original_generate = refresh.repository.update, refresh.pipeline.model.generate
    instructions = importlib.import_module('bervel.modules.ai_tender_radar.brief_ranking').INSTRUCTIONS
    def update(scope, run_id, state):
        tracked[0] = state['refresh_checkpoint']['completion_errors']['primary']
        return original_update(scope, run_id, state)
    def generate(request_id, prompt, **kwargs):
        if not request_id.startswith('brief-rank-'):
            return original_generate(request_id, prompt, **kwargs)
        active[0] += 1
        key = json.loads(prompt[len(instructions):])['candidates'][0]['id']
        try:
            sleep(.01 if key == keys[0] else .08)
            if key == keys[0]:
                raise CoreError('MODEL_INVALID_OUTPUT', 'Local primary failure')
            return original_generate(request_id, prompt, **kwargs)
        finally:
            active[0] -= 1
    module = importlib.import_module('bervel.modules.ai_tender_radar.profile_refresh')
    monkeypatch.setattr(module, 'RLock', lambda: shared_lock)
    monkeypatch.setattr(refresh.repository, 'update', update)
    monkeypatch.setattr(refresh.pipeline.model, 'generate', generate)
    execute(refresh)
    summary(refresh, failed_tenders=1, primary_failed=1)


def test_review_global_model_auth_failure_is_not_a_local_completion_warning(refresh):
    original = refresh.pipeline.model.generate
    def generate(request_id, prompt, **kwargs):
        if request_id.startswith('run-'):
            raise CoreError('MODEL_AUTH_ERROR', 'Independent review: invalid model credential')
        return original(request_id, prompt, **kwargs)
    refresh.pipeline.model.generate = generate
    with pytest.raises(CoreError) as raised:
        execute(refresh)
    assert raised.value.code == 'MODEL_AUTH_ERROR'
    assert refresh.repository.state['state'] == 'failed'


# The fixture does not connect unless the explicit db_ test is selected.
from test_radar_profiles_storage import profile_scenario, run_scenario, complete


def test_review_db_public_completion_and_current_warnings_are_durable_and_get_only(profile_scenario, monkeypatch):
    from sqlalchemy import select
    from bervel.modules.ai_tender_radar.infrastructure.runs import runs, analyses
    s = profile_scenario
    receipt = s.profiles.refresh(s.actor, 'test_api', 'review-public-completion', 0)
    pipeline = s.service.run_pipeline
    row = pipeline.repository.read(s.actor.data_scope, receipt['run_id'])
    public_summary = dict(version='profile-completion-v1', failed_tenders=3, primary_failed=1,
        detail_failed=1, analysis_failed=1, rating_failed=1, metrics_failed=1)
    errors = {stage: {} for stage in ('primary', 'detail', 'analysis', 'rating', 'metrics')}
    for stage, key in [('primary', 'no-grade'), ('detail', 'no-detail'), ('analysis', 'partial'),
                       ('rating', 'partial'), ('metrics', 'partial')]:
        errors[stage][key] = {'code': f'REVIEW_{stage.upper()}', 'message': f'Visible {stage} reason'}
    data = dict(row['data'], state='complete', phase='complete', completion_summary=public_summary,
        search_attempts=56, detail_attempts=200,
        refresh_checkpoint=dict(job_id=str(receipt['job_id']), finished=True, completion_errors=errors))
    pipeline.repository.update(s.actor.data_scope, receipt['run_id'], data)
    for key in ('no-grade', 'no-detail', 'partial', 'historical'):
        s.profiles.repository.upsert_card(receipt['run_id'], key, {'candidate_id': key,
            'brief': {'Id': key, 'OrderName': 'Болт М8'}, 'details_loaded': False,
            'analysis': None, 'state': 'primary', 'priority_score': None,
            'error': {'code': 'OLD_PRIVATE_ERROR', 'message': 'Unrelated prior error'},
            'refresh_warnings': [{'stage': 'analysis', 'code': 'OLD_PRIVATE_ERROR', 'message': 'Old'}]})
    complete(s, receipt)
    def snapshot():
        with s.engine.connect() as connection:
            return deepcopy(connection.scalar(select(runs.c.data).where(runs.c.run_id == receipt['run_id']))), \
                deepcopy(list(connection.execute(select(analyses.c.data).where(analyses.c.run_id == receipt['run_id'])).scalars()))
    before = snapshot()
    def forbidden(*args, **kwargs):
        raise AssertionError('GET of saved warning facts must not call model or source')
    monkeypatch.setattr(pipeline.model, 'generate', forbidden)
    monkeypatch.setattr(pipeline, 'sources', {})
    response = s.client.get('/api/v0.1/ai-tender-radar/runs/' + str(receipt['run_id']))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['data']['completion_summary'] == public_summary
    assert result['data']['state'] == result['data']['phase'] == 'complete'
    assert result['data']['search_attempts'] == 56 and result['data']['detail_attempts'] == 200
    cards = {card['candidate_id']: card for card in result['cards']}
    assert 'refresh_warnings' not in cards['historical']
    for key in ('no-grade', 'no-detail', 'partial'):
        assert cards[key]['refresh_warnings'] == [dict(stage=stage, **errors[stage][key])
            for stage in ('primary', 'detail', 'analysis', 'rating', 'metrics') if key in errors[stage]]
        assert cards[key]['rating']['score'] is None
    assert snapshot() == before
