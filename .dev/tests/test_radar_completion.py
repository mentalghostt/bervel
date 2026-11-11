"""Finite profile cohorts settle local errors without hiding global failures."""
from collections import Counter
from copy import deepcopy
import json
from threading import Lock
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from bervel.core.errors import CoreError
from test_named_radar_execution import tender
from test_radar_profile_refresh import refresh, execute, no_retry_wait


def cohort(env, count=20):
    keys = [f'card-{i:02}' for i in range(count)]
    env.source.briefs = [{'Id': key, 'OrderName': 'Болт М8'} for key in keys]
    env.source.details = {key: tender(key) for key in keys}
    env.job['operation']['detail_max'] = count
    return keys


def mixed_failures(env):
    from bervel.modules.ai_tender_radar.documents import TERM_FIELDS
    keys = cohort(env)
    env.source.details[keys[0]]['documents'] = [{'url': 'https://example.test/spec',
        'text': 'Наименование: Гайка М12; количество: 10; единица: шт.'}]
    generate = env.pipeline.model.generate
    attempts, lock = Counter(), Lock()

    def generated(request_id, prompt, **options):
        if request_id.startswith('documents-'):
            with lock:
                attempts['documents'] += 1
            return {'model': 'external-fake', 'answer': json.dumps({'terms': dict.fromkeys(TERM_FIELDS),
                'requirements': [], 'inventory': {'complete': None, 'rows': []}})}
        if request_id.startswith('card-rating-'):
            key = next(key for key, card in env.repository.saved.items()
                       if (card.get('individual_rating') or {}).get('pending_request_id') == request_id)
            if key == keys[1]:
                with lock:
                    attempts['rating'] += 1
                return {'answer': 'invalid', 'model': 'external-fake'}
        return generate(request_id, prompt, **options)

    env.pipeline.model.generate = generated
    return keys, attempts


def test_local_document_error_settles_entire_cohort_and_independent_rating_budget(refresh):
    keys, attempts = mixed_failures(refresh)
    execute(refresh)
    state = refresh.repository.state
    assert state['state'] == 'complete'
    assert state['completion_summary'] == {'version': 'profile-completion-v1', 'failed_tenders': 2,
        'primary_failed': 0, 'detail_failed': 0, 'analysis_failed': 1, 'rating_failed': 1, 'metrics_failed': 0}
    assert attempts == {'documents': 3, 'rating': 3}
    assert state['checkpoint']['analysis_completed'] == 19
    assert state['checkpoint']['rating_completed'] == 18
    assert state['checkpoint']['finished'] is True
    assert len(refresh.source.fetches) == 20
    assert all(refresh.repository.saved[key]['individual_rating']['status'] == 'complete' for key in keys[2:])
    assert refresh.repository.saved[keys[1]]['individual_rating']['score'] is None


def test_partial_primary_keeps_valid_details_and_never_fabricates_priority(refresh):
    generate = refresh.pipeline.model.generate

    def generated(request_id, prompt, **options):
        if '-rank-' in request_id and json.loads(prompt.partition('\nВХОД\n')[2])['candidates'][0]['id'] == 'a':
            return {'answer': 'invalid', 'model': 'external-fake'}
        return generate(request_id, prompt, **options)

    refresh.pipeline.model.generate = generated
    execute(refresh)
    assert refresh.repository.state['state'] == 'complete'
    assert refresh.repository.state['completion_summary']['primary_failed'] == 1
    assert refresh.source.fetches == ['b']
    assert refresh.repository.saved['a'].get('priority_score') is None
    assert refresh.repository.saved['b']['individual_rating']['score'] == 83


def test_metrics_persistence_failure_propagates_instead_of_becoming_card_warning(refresh):
    from bervel.modules.ai_tender_radar.metrics_refresh import refresh_metrics_cards
    execute(refresh)
    writer = refresh.repository.upsert_card

    def broken(*args):
        if 'metrics_projection' in args[-1]:
            raise OperationalError('upsert', {}, RuntimeError('database offline'))
        return writer(*args)

    refresh.repository.upsert_card = broken
    metrics_job = dict(refresh.job, operation=dict(refresh.job['operation'], kind='profile_metrics'))
    with pytest.raises(OperationalError):
        refresh_metrics_cards(refresh.pipeline, metrics_job, refresh.run, refresh.profile, refresh.stock, {}, lambda: None)


def test_rating_preflight_database_failure_is_global_not_model_unknown():
    from threading import RLock
    from bervel.modules.ai_tender_radar.individual_rating import build_card_rating_input, rate_card
    from bervel.modules.ai_tender_radar.named_runs import _CountedModel
    from test_radar_individual_rating import rating_case, Model
    snapshot = build_card_rating_input(*rating_case())

    def broken_save():
        raise OperationalError('checkpoint', {}, RuntimeError('database offline'))

    model = _CountedModel(Model([]), {}, RLock(), broken_save)
    with pytest.raises(Exception) as failure:
        rate_card(snapshot, model, 'logical', heartbeat=lambda: None, save=lambda result: None, retry_cycle='cycle')
    assert not isinstance(failure.value, CoreError) or failure.value.code != 'CARD_RATING_OUTCOME_UNKNOWN'


@pytest.mark.parametrize('code', ['MODEL_AUTH_ERROR', 'MODEL_QUOTA_EXCEEDED', 'MODEL_TLS_ERROR',
    'MODEL_UNCONFIGURED', 'INVALID_SETTINGS', 'SETTINGS_UNAVAILABLE', 'AI_RADAR_JOB_INTERRUPTED', 'USER_STOPPED'])
def test_global_failures_never_complete_with_warnings(refresh, code):
    generate = refresh.pipeline.model.generate

    def generated(request_id, prompt, **options):
        if request_id.startswith('run-'):
            raise CoreError(code, 'global failure')
        return generate(request_id, prompt, **options)

    refresh.pipeline.model.generate = generated
    with pytest.raises(CoreError) as failure:
        execute(refresh)
    assert failure.value.code == code
    assert refresh.repository.state['state'] == 'failed'
    assert not refresh.repository.state['checkpoint'].get('finished')


def test_settled_resume_never_repeats_source_or_ready_stages(refresh):
    keys, attempts = mixed_failures(refresh)
    execute(refresh)
    before = deepcopy(refresh.repository.saved)
    calls = list(refresh.pipeline.model.calls)
    source = (list(refresh.source.searches), list(refresh.source.fetches))
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.job = dict(refresh.job, job_id=uuid4(), operation=dict(refresh.job['operation'],
        refresh_id=str(refresh.job['job_id'])))
    execute(refresh)
    assert refresh.repository.saved == before
    assert refresh.pipeline.model.calls == calls
    assert (refresh.source.searches, refresh.source.fetches) == source
    assert attempts == {'documents': 3, 'rating': 3}


def test_public_warning_projection_includes_unanalyzed_cards_and_ignores_history():
    from bervel.modules.ai_tender_radar.presentation import present_refresh_warnings
    card = {'candidate_id': 'a', 'analysis': None, 'error': {'code': 'OLD_ERROR', 'message': 'old'},
            'refresh_warnings': [{'stage': 'analysis', 'code': 'OLD_ERROR', 'message': 'old'}]}
    checkpoint = {'completion_errors': {'primary': {'a': {'code': 'MODEL_INVALID_OUTPUT', 'message': 'invalid'}},
        'analysis': {'b': {'code': 'MODEL_INVALID_OUTPUT', 'message': 'other'}}}}
    assert present_refresh_warnings(card, checkpoint)['refresh_warnings'] == [
        {'stage': 'primary', 'code': 'MODEL_INVALID_OUTPUT', 'message': 'invalid'}]
    assert 'refresh_warnings' not in present_refresh_warnings(card, {})
    assert card['refresh_warnings'][0]['code'] == 'OLD_ERROR'


def test_current_v2_resume_skips_ready_cards_and_respects_exhausted_budget(refresh):
    keys = cohort(refresh, 3)
    execute(refresh)
    ready = {key: deepcopy(refresh.repository.saved[key]) for key in keys[1:]}
    refresh.repository.saved[keys[0]].update(analysis_completed_refresh_id=None, analysis_stale=True,
        analysis_refresh_error={'code': 'MODEL_INVALID_OUTPUT', 'message': 'conflicting document'})
    state = deepcopy(refresh.repository.state)
    logical = str(refresh.job['job_id'])
    state.update(state='failed', phase='failed')
    checkpoint = state['refresh_checkpoint']
    checkpoint.pop('finished')
    checkpoint['analyzed_ids'].remove(keys[0])
    checkpoint['analysis_retry']['attempts'][keys[0]] = 3
    checkpoint['completion_errors'] = {'analysis': {keys[1]: {'code': 'MODEL_INVALID_OUTPUT', 'message': 'stale'}},
        'rating': {keys[2]: {'code': 'MODEL_INVALID_OUTPUT', 'message': 'stale'}}}
    refresh.run['data'] = state
    before_calls = list(refresh.pipeline.model.calls)
    source = (list(refresh.source.searches), list(refresh.source.fetches))
    refresh.job = dict(refresh.job, job_id=uuid4(), operation=dict(refresh.job['operation'], refresh_id=logical))
    execute(refresh)
    assert refresh.repository.state['stage_versions'] == state['stage_versions']
    assert refresh.repository.state['stage_versions']['refresh'] == 'incremental-profile-v2'
    assert refresh.repository.state['completion_summary']['analysis_failed'] == 1
    assert refresh.repository.state['completion_summary']['failed_tenders'] == 1
    assert refresh.pipeline.model.calls == before_calls
    assert (refresh.source.searches, refresh.source.fetches) == source
    assert {key: refresh.repository.saved[key] for key in keys[1:]} == ready


def test_all_primary_failures_finish_with_errors_without_details_or_numeric_priority(refresh):
    refresh.pipeline.model.fail_rank = True
    execute(refresh)
    summary = refresh.repository.state['completion_summary']
    assert summary['failed_tenders'] == summary['primary_failed'] == 2
    assert refresh.repository.state['state'] == 'complete'
    assert refresh.source.fetches == []
    assert all(card.get('priority_score') is None and not card.get('individual_rating')
               for card in refresh.repository.saved.values())


def test_crash_after_local_card_save_preserves_durable_budget_on_restart(refresh):
    class ProcessDeath(BaseException):
        pass
    keys, attempts = mixed_failures(refresh)
    upsert = refresh.profiles.upsert_card
    died = False

    def crash(*args):
        nonlocal died
        value = upsert(*args)
        if not died and value['candidate_id'] == keys[0] and value.get('state') == 'failed':
            died = True
            raise ProcessDeath()
        return value

    refresh.profiles.upsert_card = crash
    with pytest.raises(ProcessDeath):
        execute(refresh)
    refresh.profiles.upsert_card = upsert
    source = (list(refresh.source.searches), list(refresh.source.fetches))
    refresh.run['data'] = deepcopy(refresh.repository.state)
    logical = str(refresh.job['job_id'])
    refresh.job = dict(refresh.job, job_id=uuid4(), operation=dict(refresh.job['operation'], refresh_id=logical))
    execute(refresh)
    assert refresh.repository.state['state'] == 'complete'
    assert refresh.repository.state['completion_summary']['failed_tenders'] == 2
    assert (refresh.source.searches, refresh.source.fetches) == source
    assert attempts == {'documents': 3, 'rating': 3}


def test_metrics_local_error_is_reported_after_full_cohort_without_historical_errors(refresh, monkeypatch):
    import bervel.modules.ai_tender_radar.metrics_refresh as metrics
    keys = cohort(refresh, 2)
    execute(refresh)
    calculate = metrics.calculate_metrics_projection

    def calculation(card, *args, **kwargs):
        if card['candidate_id'] == keys[0]:
            raise CoreError('VALIDATION_ERROR', 'missing numerical input')
        return calculate(card, *args, **kwargs)

    monkeypatch.setattr(metrics, 'calculate_metrics_projection', calculation)
    refresh.repository.saved['old'] = {'candidate_id': 'old', 'brief': {'Id': 'old', 'IsCancelled': True},
        'state': 'failed', 'analysis': None, 'details_loaded': False,
        'error': {'code': 'MODEL_INVALID_OUTPUT', 'message': 'historical'}}
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.job = dict(refresh.job, job_id=uuid4())
    refresh.source.searches.clear()
    execute(refresh)
    assert refresh.repository.state['state'] == 'complete'
    assert refresh.repository.state['completion_summary']['failed_tenders'] == 1
    assert refresh.repository.state['completion_summary']['metrics_failed'] == 1


def test_durable_primary_success_clears_a_stale_error_after_checkpoint_crash():
    from bervel.modules.ai_tender_radar.brief_ranking import rank_briefs_with_model
    from test_ai_radar_brief_ranking import brief, RankingModel
    errors = {'a': {'code': 'MODEL_INVALID_OUTPUT', 'message': 'old attempt'}}
    ranked = rank_briefs_with_model([brief('a', 'Болт М8')], {'products': []}, RankingModel(), 'logical',
        heartbeat=lambda: None, saved_evaluations={'a': {'score': 75}}, local_errors=errors)
    assert ranked[0]['rank'] == 75
    assert errors == {}


def test_completion_errors_mutate_under_the_same_lock_as_durable_state_saves(refresh, monkeypatch):
    from threading import RLock
    import bervel.modules.ai_tender_radar.profile_refresh as module
    keys = cohort(refresh, 1)
    execute(refresh)
    guard, enabled = RLock(), False

    class GuardedErrors(dict):
        def __setitem__(self, key, value):
            assert not enabled or guard._is_owned(), 'Completion map changed outside the durable-save lock'
            super().__setitem__(key, value)

    class GuardedStages(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, GuardedErrors(value) if key == 'analysis' else value)

    card = refresh.repository.saved[keys[0]]
    card.update(analysis=None, analysis_stage_cache={}, analysis_completed_refresh_id=None)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    state = refresh.run['data']
    state.update(state='failed', phase='failed')
    state['refresh_checkpoint'].pop('finished')
    state['refresh_checkpoint']['analyzed_ids'] = []
    state['refresh_checkpoint']['completion_errors'] = GuardedStages(
        state['refresh_checkpoint']['completion_errors'])
    monkeypatch.setattr(module, 'RLock', lambda: guard)
    generate = refresh.pipeline.model.generate

    def generated(request_id, prompt, **options):
        nonlocal enabled
        if request_id.startswith('run-'):
            enabled = True
            raise CoreError('MODEL_INVALID_OUTPUT', 'local failure')
        return generate(request_id, prompt, **options)

    refresh.pipeline.model.generate = generated
    execute(refresh)
    assert refresh.repository.state['state'] == 'complete'
    assert refresh.repository.state['completion_summary']['analysis_failed'] == 1


def test_known_global_rating_error_saves_failed_envelope_before_propagating():
    from bervel.modules.ai_tender_radar.individual_rating import build_card_rating_input, rate_card
    from test_radar_individual_rating import rating_case, Model
    saved = []
    with pytest.raises(CoreError, match='MODEL_AUTH_ERROR'):
        rate_card(build_card_rating_input(*rating_case()), Model(saved, error=CoreError('MODEL_AUTH_ERROR', 'denied')),
            'logical', heartbeat=lambda: None, save=lambda value: saved.append(value), retry_cycle='cycle')
    assert saved[-1]['status'] == 'failed'
    assert saved[-1]['error']['code'] == 'MODEL_AUTH_ERROR'
