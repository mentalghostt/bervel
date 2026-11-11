"""Persistent profile contracts against the dedicated PostgreSQL test database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, inspect, select

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.modules.ai_tender_radar.infrastructure.profiles import PostgresProfileRepository, profiles
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
from bervel.modules.ai_tender_radar.infrastructure.runs import analyses, runs
from bervel.modules.ai_tender_radar.profiles import RadarProfiles
from test_ai_radar_runs import run_scenario


@pytest.fixture
def profile_scenario(run_scenario):
    scenario = run_scenario
    scenario.profiles = RadarProfiles(scenario.service.run_pipeline, PostgresProfileRepository(scenario.engine))
    return scenario


def complete(scenario, job):
    with scenario.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == job['job_id']).values(state='loaded'))


def test_read_only_two_profiles_and_scope(profile_scenario):
    s = profile_scenario
    result = s.profiles.list(s.actor)['profiles']
    assert [p['source'] for p in result] == ['test_api', 'kontur_api']
    assert all(p['run_id'] is None for p in result)
    assert s.profiles.read(s.actor, 'test_api')['run'] is None
    with s.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(profiles)) == 0
    s.profiles.save_keywords(s.actor, 'test_api', 'manual', ['Болт', 'Болт', ' Гайка '])
    other = ActorContext('other', 'other-scope', s.actor.permissions)
    assert s.profiles.read(other, 'test_api')['profile']['keywords'] == []
    assert s.profiles.read(s.actor, 'kontur_api')['profile']['keywords'] == []
    assert s.profiles.read(s.actor, 'test_api')['profile']['keywords'] == ['Болт', 'Гайка']


def test_stable_run_card_and_replay_before_new_snapshot(profile_scenario):
    s = profile_scenario
    assert any(c['column_names'] == ['run_id', 'candidate_id']
               for c in inspect(s.engine).get_unique_constraints('ai_radar_analyses'))
    first = s.profiles.refresh(s.actor, 'test_api', 'stable-zero', 0)
    assert first['operation']['detail_max'] == 0
    assert first['operation']['keywords'] == []
    repository = s.profiles.repository
    a = repository.upsert_card(first['run_id'], 'same', {'brief': {'Title': 'Old'}, 'review': {'answer': 'saved'}})
    b = repository.upsert_card(first['run_id'], 'same', {'brief': {'Title': 'New'}, 'analysis_id': str(uuid4())})
    assert a['analysis_id'] == b['analysis_id']
    assert b['review'] == a['review']
    assert b['brief']['Title'] == 'New'
    complete(s, first)
    original = s.company.read
    s.company.read = lambda actor: (_ for _ in ()).throw(AssertionError('Replay must not read settings'))
    try:
        repeated = s.profiles.refresh(s.actor, 'test_api', 'stable-zero', 0)
        assert repeated['job_id'] == first['job_id']
        with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
            s.profiles.refresh(s.actor, 'test_api', 'stable-zero', None)
    finally:
        s.company.read = original
    second = s.profiles.refresh(s.actor, 'test_api', 'stable-null', None)
    assert second['job_id'] != first['job_id']
    assert second['run_id'] == first['run_id']
    with s.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(runs)) == 1
        assert conn.scalar(select(func.count()).select_from(analyses)) == 1


def test_double_command_and_schedule_rollback_when_busy(profile_scenario):
    s = profile_scenario
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: s.profiles.refresh(s.actor, 'test_api', 'double', 0), range(2)))
    assert result[0]['job_id'] == result[1]['job_id']
    schedule = dict(enabled=True, every_days=3, time='09:00', timezone='Europe/Moscow')
    with pytest.raises(CoreError, match='PROCESS_ACTIVE'):
        s.profiles.refresh(s.actor, 'test_api', 'busy', 0, schedule)
    assert s.profiles.read(s.actor, 'test_api')['profile']['schedule']['enabled'] is False
    with s.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(jobs)) == 1


@pytest.mark.parametrize('mode,words', [('manual', []), ('manual', ['']), ('manual', ['a\n']), ('manual', ['x'*501]), ('bad', []), ('auto', ['word'])])
def test_keywords_validation(profile_scenario, mode, words):
    with pytest.raises(CoreError, match='VALIDATION_ERROR'):
        profile_scenario.profiles.save_keywords(profile_scenario.actor, 'test_api', mode, words)


@pytest.mark.parametrize('value', [-1, True, 1.5, '1'])
def test_budget_validation(profile_scenario, value):
    with pytest.raises(CoreError, match='VALIDATION_ERROR'):
        profile_scenario.profiles.refresh(profile_scenario.actor, 'test_api', 'invalid', value)


def test_scheduler_busy_restart_moscow_every_days_and_no_second_day(profile_scenario):
    s = profile_scenario
    schedule = dict(enabled=True, every_days=3, time='09:00', timezone='Europe/Moscow')
    s.profiles.save_schedule(s.actor, 'test_api', 'configure-schedule', schedule, False)
    first = s.profiles.refresh(s.actor, 'test_api', 'enable-schedule', 0, schedule)
    due = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
    with s.engine.begin() as conn:
        row = conn.execute(select(profiles.c.data)).scalar_one()
        conn.execute(profiles.update().values(data=dict(row, next_due_at=due.isoformat())))
    assert s.profiles.enqueue_due(due) == 0  # Busy does not consume the due date.
    complete(s, first)
    restarted = RadarProfiles(s.service.run_pipeline, PostgresProfileRepository(s.engine))
    assert restarted.enqueue_due(due) == 1
    assert restarted.enqueue_due(due) == 0
    with s.engine.connect() as conn:
        auto = dict(conn.execute(select(jobs).where(jobs.c.command_key.like('profile-auto:%'))).mappings().one())
    assert auto['operation']['detail_max'] is None
    complete(s, auto)
    assert restarted.enqueue_due(datetime(2026,11,12,12,tzinfo=timezone.utc)) == 0
    assert restarted.enqueue_due(datetime(2026,11,13,5,59,tzinfo=timezone.utc)) == 0
    assert restarted.enqueue_due(datetime(2026,11,13,6,tzinfo=timezone.utc)) == 1


def test_snapshot_scope_and_finish_stale_job(profile_scenario):
    s = profile_scenario
    first = s.profiles.refresh(s.actor, 'test_api', 'finish', 0)
    repository = s.profiles.repository
    with pytest.raises(CoreError, match='NOT_FOUND'):
        repository.set_run_snapshot('other-scope', first['run_id'], uuid4())
    repository.finish_refresh(s.actor.data_scope, 'test_api', uuid4())
    assert s.profiles.read(s.actor, 'test_api')['profile']['last_refresh_at'] is None
    repository.finish_refresh(s.actor.data_scope, 'test_api', first['job_id'])
    assert s.profiles.read(s.actor, 'test_api')['profile']['last_refresh_at'] is not None


def test_sources_do_not_share_ids_and_keep_cache_on_refresh(profile_scenario):
    s = profile_scenario
    first = s.profiles.refresh(s.actor, 'test_api', 'source-test', 0)
    test_card = s.profiles.repository.upsert_card(first['run_id'], 'same-source-id', {'state':'primary'})
    with s.engine.begin() as conn:
        conn.execute(runs.update().where(runs.c.run_id == first['run_id']).values(
            data=runs.c.data.op('||')({'auto_keyword_cache':{'fingerprint':'cache','keywords':['bolt']}})))
    complete(s, first)
    real = s.profiles.refresh(s.actor, 'kontur_api', 'source-real', None)
    real_card = s.profiles.repository.upsert_card(real['run_id'], 'same-source-id', {'state':'primary'})
    assert test_card['analysis_id'] != real_card['analysis_id']
    assert real['run_id'] != first['run_id']
    complete(s, real)
    updated = s.profiles.refresh(s.actor, 'test_api', 'source-test-again', 0)
    with s.engine.connect() as conn:
        run_data = conn.scalar(select(runs.c.data).where(runs.c.run_id == updated['run_id']))
    assert run_data['auto_keyword_cache']['fingerprint'] == 'cache'


def test_scheduler_two_ticks_time_change_and_missed_intervals(profile_scenario):
    s = profile_scenario
    due = datetime(2026,11,10,6,tzinfo=timezone.utc)
    schedule = dict(enabled=True, every_days=2, time='09:00', timezone='Europe/Moscow')
    s.profiles.save_schedule(s.actor, 'test_api', 'configure-ticks', schedule, False, now=due-timedelta(days=1))
    enabled = s.profiles._refresh(s.actor, 'test_api', 'scheduler-enable', 0, schedule, now=due-timedelta(days=1))
    complete(s, enabled)
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda _: s.profiles.enqueue_due(due), range(2)))
    assert sum(counts) == 1
    with s.engine.connect() as conn:
        auto = dict(conn.execute(select(jobs).where(jobs.c.command_key.like('profile-auto:%'))).mappings().one())
    complete(s, auto)
    s.profiles.save_schedule(s.actor,'test_api','change-time',dict(schedule,time='15:00'),False,now=due+timedelta(hours=1))
    assert s.profiles.enqueue_due(due+timedelta(hours=6)) == 0
    assert s.profiles.enqueue_due(due+timedelta(days=20)) == 1
    assert s.profiles.enqueue_due(due+timedelta(days=20)) == 0


def test_scheduler_missing_stock_is_visible_and_not_retried_forever(profile_scenario):
    s = profile_scenario
    due = datetime(2026,11,10,6,tzinfo=timezone.utc)
    schedule = dict(enabled=True, every_days=1, time='09:00', timezone='Europe/Moscow')
    s.profiles.save_schedule(s.actor, 'test_api', 'configure-broken', schedule, False, now=due-timedelta(days=1))
    enabled = s.profiles._refresh(s.actor, 'test_api', 'scheduler-broken', 0, schedule, now=due-timedelta(days=1))
    complete(s, enabled)
    original = s.company.read
    s.company.read = lambda actor: None
    try:
        assert s.profiles.enqueue_due(due) == 0
        assert s.profiles.enqueue_due(due) == 0
    finally:
        s.company.read = original
    result = s.profiles.read(s.actor, 'test_api')
    assert result['run']['data']['error']['code'] == 'VALIDATION_ERROR'
    assert result['profile']['next_refresh_at'] == (due+timedelta(days=1)).isoformat()


@pytest.mark.parametrize('source,run_data,expected', [
    ('test_api', {'limits':[], 'effective_budgets':{'search':200,'detail':0}}, {'search':200,'detail':200}),
    ('kontur_api', {'quotas':{'search':19,'detail':8}}, {'search':19,'detail':8}),
    ('kontur_api', {}, None),
])
def test_cached_quotas_do_not_confuse_zero_budget_with_remaining(profile_scenario, source, run_data, expected):
    s = profile_scenario
    job = s.profiles.refresh(s.actor, source, 'quota-zero', 0)
    with s.engine.begin() as conn:
        conn.execute(runs.update().where(runs.c.run_id == job['run_id'])
                     .values(data=runs.c.data.op('||')(run_data)))
    s.profiles.repository.finish_refresh(s.actor.data_scope,source,job['job_id'])
    assert s.profiles.list(s.actor)['profiles'][0 if source=='test_api' else 1].get('quotas') == expected


def test_refresh_freezes_keywords_and_latest_snapshot(profile_scenario):
    s = profile_scenario
    s.profiles.save_keywords(s.actor,'test_api','manual',['Болт'])
    first = s.profiles.refresh(s.actor,'test_api','frozen',0)
    s.profiles.save_keywords(s.actor,'test_api','manual',['Гайка'])
    repeated = s.profiles.refresh(s.actor,'test_api','frozen',0)
    assert repeated['operation']['keywords'] == ['Болт']
    assert repeated['operation']['settings_snapshot_id'] == str(s.snapshot['id'])
    complete(s, first)
    second = s.profiles.refresh(s.actor,'test_api','changed',0)
    assert second['operation']['keywords'] == ['Гайка']
    assert second['operation']['search_fingerprint'] != first['operation']['search_fingerprint']
