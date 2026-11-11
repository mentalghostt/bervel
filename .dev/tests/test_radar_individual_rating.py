"""Independent card scores, production cutoffs and durable per-card recovery."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from time import monotonic, sleep
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from test_ai_radar_production import case, NOW
from test_radar_profile_refresh import refresh, execute, no_retry_wait


def implementation():
    name = 'bervel.modules.ai_tender_radar.individual_rating'
    assert importlib.util.find_spec(name) is not None, 'Individual card rating is missing'
    return importlib.import_module(name)


def rating_case(**changes):
    tender, stock, analysis, profile = case(**changes)
    local_start = datetime(2026, 11, 2, tzinfo=timezone(timedelta(hours=3))).isoformat()
    profile['production'][0]['material_ready_at'] = local_start
    tender['application_deadline'] = '2026-11-07T00:00:00+03:00'
    card = {'analysis_id': 'card-analysis', 'candidate_id': 'candidate', 'state': 'accepted',
        'tender': tender, 'analysis': analysis, 'details_loaded': True,
        'evaluated_at': local_start, 'analysis_settings_snapshot_id': 'frozen-settings',
        'analysis_completed_refresh_id': 'refresh-one',
        'metrics': {'catalog_match_percent': '100', 'catalog_match_incomplete': False,
            'stock_coverage_percent': '40', 'stock_coverage_incomplete': False},
        'opportunity': {'margin_percent': '25', 'basis': 'confirmed'}}
    return card, profile, stock


def test_submission_cutoff_is_48_calendar_hours_and_keeps_delivery_plan_unchanged():
    module = implementation()
    card, profile, stock = rating_case()
    # 60 units / 20 per day = Nov 2, 3, 4; ready Nov 5 exactly 48h before Nov 7.
    card['tender']['terms']['start_at'] = '2026-11-20T00:00:00+00:00'
    original = deepcopy((card, profile, stock))
    value = module.build_card_rating_input(card, profile, stock)
    assert value['facts']['production']['status'] == 'on_time'
    assert value['facts']['production']['cutoff'] == '2026-11-05T00:00:00+03:00'
    assert value['facts']['production']['ready_at'] == '2026-11-05T00:00:00+03:00'
    assert (card, profile, stock) == original


def test_one_second_less_buffer_is_late_and_weekend_is_not_added_to_reserve():
    module = implementation()
    card, profile, stock = rating_case()
    card['tender']['application_deadline'] = '2026-11-06T23:59:59+03:00'
    assert module.build_card_rating_input(card, profile, stock)['facts']['production']['status'] == 'late'
    card['tender']['application_deadline'] = '2026-11-09T00:00:00+03:00'
    assert module.build_card_rating_input(card, profile, stock)['facts']['production']['cutoff'] == '2026-11-07T00:00:00+03:00'


@pytest.mark.parametrize('field', ['units_per_workday', 'material_ready_at'])
def test_unknown_production_facts_are_unknown_not_an_impossible_schedule(field):
    module = implementation()
    card, profile, stock = rating_case()
    profile['production'][0][field] = None
    timing = module.build_card_rating_input(card, profile, stock)['facts']['production']
    assert timing['status'] == 'unknown'
    assert timing['ready_at'] is None


def test_stock_only_needs_no_production_route_but_incomplete_composition_is_unknown():
    module = implementation()
    card, profile, stock = rating_case(covered='100')
    profile['production'] = []
    profile['resources'] = []
    assert module.build_card_rating_input(card, profile, stock)['facts']['production']['status'] == 'not_required'
    card['tender']['limitations'] = [{'code': 'LOT_OBJECTS_MISSING', 'lot': 1}]
    assert module.build_card_rating_input(card, profile, stock)['facts']['production']['status'] == 'unknown'


def test_unknown_economics_does_not_block_known_production_timing():
    module = implementation()
    card, profile, stock = rating_case()
    profile['production'][0]['production_unit_cost'] = None
    card['tender']['terms'].update(logistics_cost=None, other_cost=None, tax_comparable=None)
    assert module.build_card_rating_input(card, profile, stock)['facts']['production']['status'] == 'on_time'


def test_shared_resource_shortage_and_partial_ready_dates_do_not_prove_whole_order_ready():
    module = implementation()
    card, profile, stock = rating_case(covered='0', rate='40')
    other = deepcopy(card['tender']['positions'][0]); other['id'] = 'q'
    card['tender']['positions'].append(other)
    other = deepcopy(card['analysis']['positions'][0]); other['id'] = 'q'
    card['analysis']['positions'].append(other)
    timing = module.build_card_rating_input(card, profile, stock)['facts']['production']
    assert timing['status'] == 'late'  # 5 resource days cannot fit Nov 2..4.
    assert timing['ready_at'] is None
    assert timing['completed_positions'] == 1
    assert timing['required_positions'] == 2


def test_fresh_brief_deadline_wins_and_unqualified_kontur_time_is_moscow():
    module = implementation()
    card, profile, stock = rating_case()
    card['brief'] = {'ApplicationDeadline': '2026-11-08T12:00:00'}
    facts = module.build_card_rating_input(card, profile, stock)['facts']
    assert facts['production']['application_deadline'] == '2026-11-08T12:00:00+03:00'
    card['brief']['ApplicationDeadline'] = 'not-a-date'
    assert module.build_card_rating_input(card, profile, stock)['facts']['production']['status'] == 'unknown'


def test_model_input_has_exactly_four_individual_facts_without_cohort_or_factory_claims():
    module = implementation()
    card, profile, stock = rating_case()
    profile['production'][0]['evidence'] = 'SIMULATION private original history'
    card['tender']['title'] = 'PRIVATE TENDER TITLE'
    value = module.build_card_rating_input(card, profile, stock)
    assert set(value['facts']) == {'product_match', 'stock_coverage', 'margin', 'production'}
    assert value['facts']['margin'] == {'percent': '25.0000', 'scope': 'confirmed'}
    payload = json.dumps(value)
    assert 'cohort' not in payload and 'percentile' not in payload and 'SIMULATION' not in payload
    assert 'PRIVATE' not in payload and 'data_basis' not in payload
    assert module.build_card_rating_input(card, profile, stock) == value


class Model:
    def __init__(self, saved, answer='83', error=None):
        self.saved, self.answer, self.error, self.calls = saved, answer, error, []

    def generate(self, request_id, prompt, **options):
        assert self.saved[-1]['status'] == 'running'
        assert self.saved[-1]['pending_request_id'] == request_id
        assert options['web_search'] is False and options['output_schema'] is None
        self.calls.append((request_id, prompt))
        if self.error:
            raise self.error
        return {'model': 'same-configured-model', 'answer': self.answer}


def run_rating(*, answer='83', error=None, checkpoint=None, cycle='cycle-one'):
    module = implementation()
    value = module.build_card_rating_input(*rating_case())
    saved = []
    model = Model(saved, answer=answer, error=error)
    result = module.rate_card(value, model, 'refresh-one', heartbeat=lambda: None,
        save=lambda v: saved.append(deepcopy(v)), checkpoint=checkpoint, retry_cycle=cycle)
    return result, saved, model


def test_score_is_model_number_not_formula_and_request_id_is_bounded():
    result, saved, model = run_rating()
    assert result['status'] == 'complete' and result['score'] == 83
    assert result['model'] == 'same-configured-model'
    assert len(model.calls) == 1 and len(model.calls[0][0]) <= 128
    assert saved[0]['pending_request_id'] == model.calls[0][0]


@pytest.mark.parametrize('answer', ['true', '83.0', '"83"', '{"score":83}', '101', '-1', '83 extra'])
def test_malformed_score_is_not_saved_as_zero_or_success(answer):
    result, saved, model = run_rating(answer=answer)
    assert result['status'] == 'failed' and result['score'] is None
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_completed_score_replay_calls_no_model_and_unknown_needs_explicit_new_cycle():
    complete, _, _ = run_rating()
    result, saved, model = run_rating(checkpoint=complete)
    assert result == complete and model.calls == []
    unknown, _, _ = run_rating(error=RuntimeError('transport outcome unknown'))
    assert unknown['status'] == 'unknown'
    result, _, model = run_rating(checkpoint=unknown)
    assert result['status'] == 'unknown' and model.calls == []
    result, _, model = run_rating(checkpoint=unknown, cycle='explicit-resume-cycle')
    assert result['status'] == 'complete' and len(model.calls) == 1


def test_stale_score_does_not_overlay_changed_deadline_or_metrics():
    module = implementation()
    card, profile, stock = rating_case()
    score, _, _ = run_rating()
    card['individual_rating'] = score
    assert module.saved_rating(card) == {'status': 'complete', 'score': 83}
    card['brief'] = {'ApplicationDeadline': '2026-11-09T00:00:00+00:00'}
    assert module.saved_rating(card) == {'status': 'stale', 'score': None, 'previous_score': 83}
    card.pop('brief')
    card['metrics']['stock_coverage_percent'] = '20'
    assert module.saved_rating(card) == {'status': 'stale', 'score': None, 'previous_score': 83}


def with_card_scores(env, rating=None):
    generate = env.pipeline.model.generate
    rating_calls = []

    def generated(request_id, prompt, **options):
        if request_id.startswith('card-rating-'):
            rating_calls.append(request_id)
            assert any(card.get('analysis_completed_refresh_id') and card.get('individual_rating', {}).get(
                'pending_request_id') == request_id for card in env.repository.saved.values())
            return rating(request_id, prompt, **options) if rating else {'answer': '83', 'model': 'same-model'}
        return generate(request_id, prompt, **options)

    env.pipeline.model.generate = generated
    return rating_calls


def resume(env, *, explicit=False):
    original = env.job
    env.run['data'] = deepcopy(env.repository.state)
    env.job = dict(original, job_id=uuid4(), operation=dict(original['operation'],
        refresh_id=original['operation'].get('refresh_id') or str(original['job_id']),
        resume_of=str(original['job_id'])))
    if explicit:
        env.job['operation']['priority_retry_cycle'] = 'explicit-next-cycle'


def test_ten_bounded_lifecycles_save_analysis_before_rating_and_publish_before_peers(refresh):
    from test_named_radar_execution import tender
    env = refresh
    keys = [f'card-{n:02}' for n in range(20)]
    env.source.briefs = [{'Id': key, 'OrderName': 'Болт М8'} for key in keys]
    env.source.details = {key: tender(key) for key in keys}
    env.job['operation']['detail_max'] = 20
    ten_started, release_fast, release_peers, release_rating, score_saved = Event(), Event(), Event(), Event(), Event()
    lock = Lock()
    active = maximum = entered = 0
    generate = env.pipeline.model.generate
    upsert = env.profiles.upsert_card

    def persist(*args):
        value = upsert(*args)
        if value.get('individual_rating', {}).get('status') == 'complete':
            score_saved.set()
        return value

    def controlled(request_id, prompt, **options):
        nonlocal active, maximum, entered
        if '-rank-' in request_id:
            return generate(request_id, prompt, **options)
        with lock:
            active += 1
            maximum = max(maximum, active)
            if request_id.startswith('run-'):
                entered += 1
                if entered == 10:
                    ten_started.set()
        try:
            if request_id.startswith('card-rating-'):
                assert any(card.get('analysis_completed_refresh_id') and card.get('individual_rating', {}).get(
                    'pending_request_id') == request_id for card in env.repository.saved.values())
                assert release_rating.wait(4)
                return {'answer': '83', 'model': 'same-model'}
            assert (release_fast if '-card-00-' in request_id else release_peers).wait(4)
            return generate(request_id, prompt, **options)
        finally:
            with lock:
                active -= 1

    env.profiles.upsert_card = persist
    env.pipeline.model.generate = controlled
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(execute, env)
        try:
            assert ten_started.wait(3), 'The first ten full analyses did not overlap'
            assert entered == 10
            release_fast.set()
            deadline = monotonic() + 3
            while monotonic() < deadline and env.repository.saved['card-00'].get('individual_rating', {}).get('status') != 'running':
                sleep(.01)
            assert env.repository.saved['card-00']['analysis_completed_refresh_id']
            assert entered == 10  # Rating occupies its lifecycle slot; no 11th is queued.
            release_rating.set()
            assert score_saved.wait(3)
            assert not release_peers.is_set() and not future.done()
        finally:
            release_fast.set(); release_peers.set(); release_rating.set()
        future.result(timeout=5)
    assert maximum == 10
    assert env.pipeline.ratings == []
    assert env.repository.state['checkpoint']['rating_completed'] == 20
    assert all(card['individual_rating']['score'] == 83 for card in env.repository.saved.values())


def test_unknown_rating_preserves_analysis_and_explicit_resume_only_rates(refresh):
    env = refresh
    calls = with_card_scores(env, lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('outcome unknown')))
    execute(env)
    assert env.repository.state['state'] == 'complete'
    assert env.repository.state['completion_summary']['rating_failed'] == 1
    analysis = deepcopy(env.repository.saved['b']['analysis'])
    model_calls = list(env.pipeline.model.calls)
    fetches = list(env.source.fetches)
    assert env.repository.saved['b']['analysis_completed_refresh_id']
    assert env.repository.saved['b']['individual_rating']['status'] == 'unknown'
    resume(env)
    execute(env)
    assert len(calls) == 1
    # A pre-upgrade failed receipt may still be explicitly recovered; only its
    # unfinished score gets a new cycle, without repeating the saved analysis.
    env.repository.state['state'] = 'failed'
    env.repository.state['refresh_checkpoint'].pop('finished')
    resume(env, explicit=True)
    with_card_scores(env)
    execute(env)
    assert env.repository.saved['b']['analysis'] == analysis
    assert env.source.fetches == fetches
    assert env.pipeline.model.calls == model_calls
    assert env.repository.saved['b']['individual_rating']['score'] == 83
    assert env.repository.state['checkpoint']['analysis_completed'] == 1
    assert env.repository.state['checkpoint']['rating_completed'] == 1


def test_committed_rating_survives_death_before_checkpoint_without_another_model(refresh):
    class ProcessDeath(BaseException):
        pass
    env = refresh
    calls = with_card_scores(env)
    upsert = env.profiles.upsert_card

    def crash(*args):
        value = upsert(*args)
        if value.get('individual_rating', {}).get('status') == 'complete':
            raise ProcessDeath()
        return value

    env.profiles.upsert_card = crash
    with pytest.raises(ProcessDeath):
        execute(env)
    before = list(env.pipeline.model.calls)
    env.profiles.upsert_card = upsert
    resume(env)
    execute(env)
    assert len(calls) == 1 and env.pipeline.model.calls == before
    assert env.repository.state['checkpoint']['rating_completed'] == 1


def test_legacy_refresh_resume_keeps_all_stage_versions_and_comparative_rating(refresh):
    env = refresh
    with_card_scores(env)
    execute(env)
    resume(env)
    env.run['data']['stage_versions'].pop('rating', None)
    env.run['data']['stage_versions']['matching'] = 'historical-matching'
    env.run['data']['refresh_checkpoint'].pop('finished')
    env.run['data']['refresh_checkpoint'].pop('rating_version', None)
    env.repository.saved['b'].pop('individual_rating')
    env.repository.saved['b'].pop('rating_version', None)
    original = deepcopy(env.run['data']['stage_versions'])
    execute(env)
    assert env.repository.state['stage_versions'] == original
    assert len(env.pipeline.ratings) == 1


def test_old_comparative_report_does_not_overwrite_saved_individual_score():
    module = implementation()
    from bervel.modules.ai_tender_radar.comparative_rating import apply_rating, build_snapshot
    card, _, _ = rating_case()
    card['individual_rating'] = run_rating()[0]
    snapshot = build_snapshot([card])
    report = {'data': {'kind': 'rating', 'result': dict(snapshot, status='complete',
        ratings=[{'id': card['analysis_id'], 'score': 2}])}}
    apply_rating([card], [report])
    assert card['rating'] == {'status': 'complete', 'score': 83, 'prompt_version': module.PROMPT_VERSION}


def test_transient_rating_retries_only_rating_with_durable_three_attempt_budget(refresh):
    env = refresh
    calls = with_card_scores(env, lambda *args, **kwargs: (_ for _ in ()).throw(
        CoreError('MODEL_UNAVAILABLE', 'provider busy')))
    execute(env)
    assert env.repository.state['state'] == 'complete'
    assert env.repository.state['completion_summary']['rating_failed'] == 1
    assert len(calls) == 3
    assert len(set(calls)) == 3
    before = list(env.pipeline.model.calls)
    assert env.repository.saved['b']['individual_rating']['attempts'] == 3
    resume(env)
    execute(env)
    assert len(calls) == 3
    env.repository.state['state'] = 'failed'
    env.repository.state['refresh_checkpoint'].pop('finished')
    resume(env, explicit=True)
    with_card_scores(env)
    execute(env)
    assert env.pipeline.model.calls == before
    assert env.repository.saved['b']['individual_rating']['attempts'] == 1


def test_equivalent_absolute_deadlines_use_the_same_moscow_work_calendar():
    module = implementation()
    card, profile, stock = rating_case(covered='0', required='100', rate='100')
    card['evaluated_at'] = '2026-11-06T00:00:00+00:00'
    profile['production'][0]['material_ready_at'] = card['evaluated_at']
    outcomes = []
    for deadline in ['2026-11-11T00:00:00+03:00', '2026-11-10T21:00:00+00:00']:
        card['tender']['application_deadline'] = deadline
        outcomes.append(module.build_card_rating_input(card, profile, stock)['facts']['production'])
    assert [value['status'] for value in outcomes] == ['late', 'late']
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize('corrupt', ['prompt_version', 'fingerprint', 'input_prompt_version', 'input_fingerprint', 'score'])
def test_invalid_saved_envelope_is_never_presented_or_reused_as_complete(corrupt):
    module = implementation()
    result = run_rating()[0]
    if corrupt.startswith('input_'):
        result['input'][corrupt.removeprefix('input_')] = 'invalid'
    else:
        result[corrupt] = True if corrupt == 'score' else 'invalid'
    card, _, _ = rating_case()
    card['individual_rating'] = result
    assert module.saved_rating(card) == {'status': 'stale', 'score': None}
    with pytest.raises(CoreError, match='VALIDATION_ERROR'):
        run_rating(checkpoint=result)
