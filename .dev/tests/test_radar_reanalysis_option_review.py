"""Independent v3 cohort and durable detail-boundary review; fake external I/O."""
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from bervel.modules.ai_tender_radar.individual_rating import build_card_rating_input
from bervel.modules.ai_tender_radar.metrics_refresh import full_input_fingerprint
from test_named_radar_execution import tender
from test_radar_profile_refresh import refresh, execute, next_refresh, no_retry_wait


def seed_saved(env, count=200, *, older_day=False):
    """Create independent saved cards from one genuinely completed fixture lifecycle."""
    execute(env)
    template = deepcopy(env.repository.saved['b'])
    env.repository.saved.clear()
    for index in range(count):
        key = f'old-{index:03d}'
        card = deepcopy(template)
        card.update(candidate_id=key, analysis_id=str(uuid4()))
        card['brief'] = {'Id': key, 'OrderName': 'Болт М8'}
        card['priority_evidence']['id'] = key
        if older_day:
            card['evaluated_at'] = (datetime.fromisoformat(card['evaluated_at']) - timedelta(days=1)).isoformat()
            card['full_input_fingerprint'] = full_input_fingerprint(card, card['analysis_metrics_dependencies'],
                now=datetime.fromisoformat(card['evaluated_at']))
        snapshot = build_card_rating_input(card, env.profile, env.stock)
        card['individual_rating'].update(input=snapshot, fingerprint=snapshot['fingerprint'])
        env.repository.saved[key] = card
    env.source.fetches.clear()
    env.pipeline.model.calls.clear()
    env.pipeline.model.ranking_ids.clear()
    return deepcopy(env.repository.saved)


def arrange_source(env, old_count, new_count):
    keys = [f'old-{n:03d}' for n in range(old_count)] + [f'new-{n:03d}' for n in range(new_count)]
    env.source.briefs = [{'Id': key, 'OrderName': 'Болт М8'} for key in keys]
    env.source.details = {key: tender(key) for key in keys}


def trace_analyses(monkeypatch):
    from bervel.modules.ai_tender_radar import profile_refresh
    original, called = profile_refresh._analyze, []
    def traced(card, *args, **kwargs):
        called.append(card['candidate_id'])
        return original(card, *args, **kwargs)
    monkeypatch.setattr(profile_refresh, '_analyze', traced)
    return called


@pytest.mark.parametrize('reanalyze', [False, True])
def test_review_400_cohort_exact_flag_semantics(refresh, monkeypatch, reanalyze):
    old = seed_saved(refresh, older_day=not reanalyze)
    arrange_source(refresh, 200, 200)
    if reanalyze:
        # ALL includes an inactive full card; the other 199 have unchanged inputs.
        refresh.source.briefs[0]['ApplicationDeadline'] = '2000-01-01T00:00:00Z'
    else:
        refresh.stock['rows'][0]['free_quantity'] = '1'
    called = trace_analyses(monkeypatch)
    from bervel.modules.ai_tender_radar import review
    reviewed, original_review = [], review.review_card
    def trace_review(card, *args, **kwargs):
        reviewed.append(card['candidate_id'])
        return original_review(card, *args, **kwargs)
    monkeypatch.setattr(review, 'review_card', trace_review)
    next_refresh(refresh, detail_max=200, refresh_version='incremental-profile-v3', reanalyze_existing=reanalyze)
    execute(refresh)
    selected = set(refresh.repository.saved) if reanalyze else {f'new-{n:03d}' for n in range(200)}
    assert set(called) == selected
    assert len(called) == len(selected)
    assert set(reviewed) == selected and len(reviewed) == len(selected)
    assert sum(request.startswith('card-rating-') for request in refresh.pipeline.model.calls) == len(selected)
    assert set(refresh.source.fetches) == {f'new-{n:03d}' for n in range(200)}
    checkpoint = refresh.repository.state['refresh_checkpoint']
    assert checkpoint['analysis_total'] == checkpoint['analysis_completed'] == len(selected)
    assert checkpoint['rating_total'] == checkpoint['rating_completed'] == len(selected)
    logical = str(refresh.job['job_id'])
    assert all(refresh.repository.saved[key]['details_loaded_refresh_id'] == logical for key in refresh.source.fetches)
    assert all(refresh.repository.saved[key]['individual_rating_completed_refresh_id'] == logical for key in selected)
    if not reanalyze:
        for key, before in old.items():
            current = refresh.repository.saved[key]
            for field in ('analysis', 'review', 'individual_rating', 'evaluated_at', 'analysis_settings_snapshot_id'):
                assert current[field] == before[field], (key, field)
            assert current['metrics_projection']['coverage']['percent'] == '10'


def test_review_false_zero_new_details_has_zero_model_calls_but_updates_metrics(refresh, monkeypatch):
    old = seed_saved(refresh, count=3, older_day=True)
    arrange_source(refresh, 3, 0)
    refresh.stock['rows'][0]['free_quantity'] = '1'
    called = trace_analyses(monkeypatch)
    next_refresh(refresh, detail_max=0, refresh_version='incremental-profile-v3', reanalyze_existing=False)
    execute(refresh)
    assert called == refresh.pipeline.model.calls == refresh.source.fetches == []
    assert refresh.repository.state['refresh_checkpoint']['analysis_total'] == 0
    assert refresh.repository.state['refresh_checkpoint']['rating_total'] == 0
    for key, before in old.items():
        assert refresh.repository.saved[key]['analysis'] == before['analysis']
        assert refresh.repository.saved[key]['metrics_projection']['coverage']['percent'] == '10'


def test_review_detail_write_checkpoint_gap_keeps_fresh_cohort_and_never_refetches(refresh, monkeypatch):
    seed_saved(refresh, count=1)
    arrange_source(refresh, 1, 1)
    next_refresh(refresh, detail_max=1, refresh_version='incremental-profile-v3', reanalyze_existing=False)
    logical = str(refresh.job['job_id'])
    original = refresh.profiles.upsert_card
    class PowerLoss(BaseException):
        pass
    def power_loss(run_id, key, card):
        result = original(run_id, key, card)
        if key == 'new-000' and card.get('details_loaded'):
            raise PowerLoss()
        return result
    monkeypatch.setattr(refresh.profiles, 'upsert_card', power_loss)
    with pytest.raises(PowerLoss):
        execute(refresh)
    durable = refresh.repository.saved['new-000']
    assert durable['details_loaded_refresh_id'] == logical
    assert durable['raw_tender'] and durable['tender'] and durable['details_loaded']
    assert refresh.source.fetches == ['new-000']
    monkeypatch.setattr(refresh.profiles, 'upsert_card', original)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.job = dict(refresh.job, job_id=uuid4(), operation=dict(refresh.job['operation'], refresh_id=logical))
    called = trace_analyses(monkeypatch)
    execute(refresh)
    assert called == ['new-000']
    assert refresh.source.fetches == ['new-000']
    after_calls = deepcopy(refresh.pipeline.model.calls)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    execute(refresh)
    assert refresh.pipeline.model.calls == after_calls
    assert called == ['new-000']


def test_review_preaccepted_v2_keeps_its_old_changed_inputs_algorithm(refresh, monkeypatch):
    seed_saved(refresh, count=2, older_day=True)
    arrange_source(refresh, 2, 0)
    refresh.stock['rows'][0]['free_quantity'] = '1'
    called = trace_analyses(monkeypatch)
    next_refresh(refresh, detail_max=0, refresh_version='incremental-profile-v2')
    refresh.job['operation'].pop('reanalyze_existing', None)
    execute(refresh)
    assert set(called) == {'old-000', 'old-001'}
    assert refresh.repository.state['stage_versions']['refresh'] == 'incremental-profile-v2'


def test_review_true_does_not_reuse_calculation_or_review_cache_from_another_refresh(refresh, monkeypatch):
    from bervel.modules.ai_tender_radar import named_runs, review
    from bervel.modules.ai_tender_radar.metrics_refresh import fingerprint
    seed_saved(refresh, count=1)
    arrange_source(refresh, 1, 0)
    card = refresh.repository.saved['old-000']
    cache = card['analysis_stage_cache']
    cache['calculation'] = {key: deepcopy(card[key]) for key in named_runs._CALCULATION_FIELDS if key in card}
    cache['review_result'] = {'state': 'complete', 'decision': 'review', 'recommendation': 'Old cached review'}
    cache['review_input_fingerprint'] = fingerprint([cache['input_fingerprint'],
        refresh.repository.state['stage_versions']['review'], card['brief'], card.get('report_summary')])
    original_plan, original_review = named_runs.evaluate_fulfillment, review.review_card
    plans, reviews = [], []
    def plan(*args, **kwargs):
        plans.append(True)
        return original_plan(*args, **kwargs)
    def review_again(value, *args, **kwargs):
        reviews.append(value['candidate_id'])
        return original_review(value, *args, **kwargs)
    monkeypatch.setattr(named_runs, 'evaluate_fulfillment', plan)
    monkeypatch.setattr(review, 'review_card', review_again)
    next_refresh(refresh, detail_max=0, refresh_version='incremental-profile-v3', reanalyze_existing=True)
    execute(refresh)
    assert plans, 'A new logical TRUE refresh must calculate again even with an old completed stage cache'
    assert reviews == ['old-000']
    assert sum(request.startswith('card-rating-') for request in refresh.pipeline.model.calls) == 1


@pytest.mark.parametrize('value', [False, True, 'omitted'])
def test_review_http_preserves_omission_and_strict_boolean(value):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bervel.core.models import ActorContext
    from bervel.modules.ai_tender_radar.profile_api import profiles_router
    missing = object()
    received = []
    class Service:
        def refresh(self, actor, source, key, detail_max, schedule=None, reanalyze_existing=missing):
            received.append(reanalyze_existing)
            return {'job_id': uuid4(), 'run_id': uuid4(), 'state': 'queued'}
    app = FastAPI()
    app.include_router(profiles_router(Service(), lambda: ActorContext('review', 'review', frozenset())))
    body = {'command_key': 'exact-command', 'detail_max': 0}
    if value != 'omitted':
        body['reanalyze_existing'] = value
    response = TestClient(app).post('/api/v0.1/ai-tender-radar/profiles/test_api/refresh', json=body)
    assert response.status_code == 202, response.text
    assert len(received) == 1
    assert received[0] is (missing if value == 'omitted' else value)


@pytest.mark.parametrize('value', [None, 0, 1, 'false', 'true', [], {}])
def test_review_http_rejects_non_boolean_reanalysis(value):
    from pydantic import ValidationError
    from bervel.modules.ai_tender_radar.profile_api import ProfileRefreshBody
    with pytest.raises(ValidationError):
        ProfileRefreshBody.model_validate({'command_key': 'invalid', 'detail_max': 0, 'reanalyze_existing': value})


@pytest.mark.parametrize('value', [False, True, None, 0, 1, 'false', [], {}])
def test_review_schedule_http_is_config_only_and_strict(value):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bervel.core.models import ActorContext
    from bervel.modules.ai_tender_radar.profile_api import profiles_router
    received = []
    class Service:
        def save_schedule(self, actor, source, command_key, schedule, reanalyze_existing):
            received.append((source, command_key, schedule, reanalyze_existing))
            return {'source': source, 'schedule': schedule, 'auto_reanalyze_existing': reanalyze_existing}
    app = FastAPI()
    app.include_router(profiles_router(Service(), lambda: ActorContext('review', 'review', frozenset())))
    schedule = dict(enabled=False, every_days=3, time='12:34', timezone='Europe/Moscow')
    response = TestClient(app).post('/api/v0.1/ai-tender-radar/profiles/test_api/schedule', json={
        'command_key': 'schedule-exact', 'schedule': schedule, 'reanalyze_existing': value})
    if type(value) is bool:
        assert response.status_code == 200, response.text
        assert received == [('test_api', 'schedule-exact', schedule, value)]
        assert 'job_id' not in response.json()
    else:
        assert response.status_code == 422, response.text
        assert received == []


# These fixtures only connect when a db_ test is explicitly selected.
from test_radar_profiles_storage import profile_scenario, run_scenario, complete


def schedule_value(enabled=False, time='12:34'):
    return dict(enabled=enabled, every_days=2, time=time, timezone='Europe/Moscow')


def test_review_db_schedule_saves_while_active_without_jobs_source_or_company_reads(profile_scenario, monkeypatch):
    from sqlalchemy import select
    from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
    s = profile_scenario
    active = s.profiles.refresh(s.actor, 'test_api', 'active-manual', 0, reanalyze_existing=True)
    with s.engine.connect() as conn:
        before = list(conn.execute(select(jobs)).mappings())
    monkeypatch.setattr(s.profiles.pipeline, 'sources', {})
    def forbidden(*args, **kwargs):
        raise AssertionError('Saving schedule must not read company settings or execute work')
    monkeypatch.setattr(s.profiles.pipeline.settings, 'read', forbidden)
    result = s.profiles.save_schedule(s.actor, 'test_api', 'independent-auto', schedule_value(True), False)
    assert result['reanalyze_existing'] is True
    assert result['auto_reanalyze_existing'] is False
    assert result['schedule'] == schedule_value(True)
    with s.engine.connect() as conn:
        assert list(conn.execute(select(jobs)).mappings()) == before
    assert s.service.jobs.read(s.actor.data_scope, active['job_id'])['state'] == 'queued'


def test_review_db_old_config_receipt_never_rolls_back_new_settings(profile_scenario):
    from bervel.core.errors import CoreError
    s = profile_scenario
    first = s.profiles.save_schedule(s.actor, 'test_api', 'config-A', schedule_value(), False)
    second = s.profiles.save_schedule(s.actor, 'test_api', 'config-B', schedule_value(True, '13:35'), True)
    assert s.profiles.save_schedule(s.actor, 'test_api', 'config-A', schedule_value(), False) == first
    assert s.profiles.read(s.actor, 'test_api')['profile'] == second
    assert s.profiles.read(s.actor, 'kontur_api')['profile']['auto_reanalyze_existing'] is False
    with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
        s.profiles.save_schedule(s.actor, 'test_api', 'config-A', schedule_value(), True)


def test_review_db_concurrent_duplicate_config_is_one_atomic_result(profile_scenario):
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy import select, func
    from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
    s = profile_scenario
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: s.profiles.save_schedule(s.actor, 'test_api',
            'same-auto-command', schedule_value(True), True), range(2)))
    assert values[0] == values[1]
    assert s.profiles.read(s.actor, 'test_api')['profile'] == values[0]
    with s.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(jobs)) == 0


def test_review_db_manual_legacy_schedule_body_cannot_change_auto_preferences(profile_scenario):
    s = profile_scenario
    saved = s.profiles.save_schedule(s.actor, 'test_api', 'auto-before-manual', schedule_value(True), True)
    job = s.profiles.refresh(s.actor, 'test_api', 'legacy-shaped-manual', 0,
        schedule_value(False, '01:23'), reanalyze_existing=False)
    after = s.profiles.read(s.actor, 'test_api')['profile']
    assert after['schedule'] == saved['schedule']
    assert after['next_refresh_at'] == saved['next_refresh_at']
    assert after['auto_reanalyze_existing'] is True
    assert after['reanalyze_existing'] is False
    assert job['operation']['reanalyze_existing'] is False
    assert job['operation']['profile_request']['schedule'] == schedule_value(False, '01:23')
    assert s.profiles.refresh(s.actor, 'test_api', 'legacy-shaped-manual', 0,
        schedule_value(False, '01:23'), reanalyze_existing=False)['job_id'] == job['job_id']


def test_review_db_automatic_uses_auto_choice_without_overwriting_manual_choice(profile_scenario):
    from datetime import datetime, timezone
    from sqlalchemy import select
    from bervel.modules.ai_tender_radar.infrastructure.profiles import profiles
    from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
    s = profile_scenario
    manual = s.profiles.refresh(s.actor, 'test_api', 'manual-false', 0, reanalyze_existing=False)
    complete(s, manual)
    s.profiles.save_schedule(s.actor, 'test_api', 'auto-true', schedule_value(True), True)
    due = datetime(2026, 11, 12, 10, tzinfo=timezone.utc)
    with s.engine.begin() as conn:
        row = conn.execute(select(profiles).where(profiles.c.source == 'test_api')).mappings().one()
        conn.execute(profiles.update().where(profiles.c.source == 'test_api', profiles.c.data_scope == s.actor.data_scope).values(
            data=dict(row['data'], next_due_at=due.isoformat())))
    assert s.profiles.enqueue_due(due) == 1
    with s.engine.connect() as conn:
        operation = conn.scalar(select(jobs.c.operation).where(jobs.c.command_key.like('profile-auto:%')))
    assert operation['reanalyze_existing'] is True
    assert operation['refresh_version'] == 'incremental-profile-v3'
    profile = s.profiles.read(s.actor, 'test_api')['profile']
    assert profile['reanalyze_existing'] is False
    assert profile['auto_reanalyze_existing'] is True


@pytest.mark.parametrize('permission,enabled', [('settings:write', False), ('process:start', True), ('artifact:read', True), ('settings:read', True)])
def test_review_db_schedule_permission_boundaries(profile_scenario, permission, enabled):
    from bervel.core.models import ActorContext
    from bervel.core.errors import CoreError
    s = profile_scenario
    actor = ActorContext('restricted', s.actor.data_scope, s.actor.permissions - {permission})
    with pytest.raises(CoreError, match='FORBIDDEN'):
        s.profiles.save_schedule(actor, 'test_api', 'must-be-denied', schedule_value(enabled), True)
    assert s.profiles.read(s.actor, 'test_api')['profile']['schedule']['enabled'] is False

