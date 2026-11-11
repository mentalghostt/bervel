"""Strict request/replay and per-source remembered reanalysis preference."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from bervel.modules.ai_tender_radar.infrastructure.profiles import profiles
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
from test_radar_profiles_api import client, profile_scenario, run_scenario
from test_radar_profiles_storage import complete


API = '/api/v0.1/ai-tender-radar/profiles'


@pytest.mark.parametrize('value', [None, 0, 1, 'false', [], {}])
def test_reanalysis_flag_is_a_strict_boolean(client, value):
    assert client.post(API+'/test_api/refresh', json={'command_key':'strict-flag',
        'detail_max':0, 'reanalyze_existing':value}).status_code == 422


def test_explicit_flag_is_pinned_remembered_and_idempotent_per_source(client, profile_scenario):
    s = profile_scenario
    assert client.get(API+'/test_api').json()['profile']['reanalyze_existing'] is False
    body = {'command_key':'true-flag', 'detail_max':0, 'reanalyze_existing':True}
    response = client.post(API+'/test_api/refresh', json=body)
    assert response.status_code == 202, response.text
    first = response.json()
    job = s.service.jobs.read(s.actor.data_scope, UUID(first['job_id']))
    assert job['operation']['reanalyze_existing'] is True
    assert job['operation']['refresh_version'] == 'incremental-profile-v3'
    assert job['operation']['profile_request']['reanalyze_existing'] is True
    assert client.get(API+'/test_api').json()['profile']['reanalyze_existing'] is True
    assert client.get(API+'/kontur_api').json()['profile']['reanalyze_existing'] is False
    assert client.post(API+'/test_api/refresh', json=body).json() == first
    assert client.post(API+'/test_api/refresh', json=dict(body, reanalyze_existing=False)).status_code == 409
    complete(s, {'job_id':UUID(first['job_id'])})
    omitted = client.post(API+'/test_api/refresh', json={'command_key':'omitted-after-true', 'detail_max':0})
    assert omitted.status_code == 202
    operation = s.service.jobs.read(s.actor.data_scope, UUID(omitted.json()['job_id']))['operation']
    assert operation['reanalyze_existing'] is True
    assert 'reanalyze_existing' not in operation['profile_request']


def test_old_omitted_body_replays_legacy_operation_without_adding_false(client, profile_scenario):
    s = profile_scenario
    body = {'command_key':'legacy-v2-omitted', 'detail_max':0}
    first = client.post(API+'/test_api/refresh', json=body).json()
    with s.engine.begin() as conn:
        operation = conn.scalar(select(jobs.c.operation).where(jobs.c.job_id == UUID(first['job_id'])))
        operation.pop('reanalyze_existing', None)
        operation['refresh_version'] = 'incremental-profile-v2'
        conn.execute(jobs.update().where(jobs.c.job_id == UUID(first['job_id'])).values(operation=operation))
    assert client.post(API+'/test_api/refresh', json=body).json() == first
    replay = s.service.jobs.read(s.actor.data_scope, UUID(first['job_id']))['operation']
    assert replay == operation
    assert client.post(API+'/test_api/refresh', json=dict(body, reanalyze_existing=False)).status_code == 409


def test_auto_refresh_uses_remembered_flag_but_resume_keeps_its_own_mode(profile_scenario):
    s = profile_scenario
    due = datetime(2026,11,11,6,tzinfo=timezone.utc)
    schedule = {'enabled':True,'every_days':1,'time':'09:00','timezone':'Europe/Moscow'}
    s.profiles.save_schedule(s.actor, 'test_api', 'save-auto-policy', schedule, True, now=due-timedelta(days=1))
    first = s.profiles._refresh(s.actor, 'test_api', 'remember-auto-true', 0,
        now=due-timedelta(days=1), reanalyze_existing=False)
    complete(s, first)
    assert s.profiles.enqueue_due(due) == 1
    with s.engine.begin() as conn:
        auto = dict(conn.execute(select(jobs).where(jobs.c.command_key.like('profile-auto:%'))).mappings().one())
        assert auto['operation']['reanalyze_existing'] is True
        conn.execute(jobs.update().where(jobs.c.job_id == auto['job_id']).values(state='failed',
            error={'code':'PROFILE_REFRESH_FAILED','message':'fixture'}))
        data = conn.scalar(select(profiles.c.data).where(profiles.c.source == 'test_api'))
        conn.execute(profiles.update().where(profiles.c.source == 'test_api').values(data=dict(data, reanalyze_existing=False)))
    resumed = s.profiles.resume(s.actor,'test_api','resume-pinned-true')
    assert resumed['operation']['reanalyze_existing'] is True
    assert resumed['operation']['refresh_version'] == 'incremental-profile-v3'


def test_schedule_is_independent_config_and_replay_never_rolls_back_new_policy(client, profile_scenario):
    s = profile_scenario
    path = API+'/test_api/schedule'
    schedule = {'enabled':True,'every_days':2,'time':'09:00','timezone':'Europe/Moscow'}
    initial = s.profiles.refresh(s.actor, 'test_api', 'active-manual-mode', 0, reanalyze_existing=True)
    original = s.service.jobs.read(s.actor.data_scope, initial['job_id'])['operation']
    s.service.run_pipeline.sources = {}
    body = {'command_key':'auto-policy-a', 'schedule':schedule, 'reanalyze_existing':False}
    first = client.post(path, json=body)
    assert first.status_code == 200, first.text
    assert first.json()['reanalyze_existing'] is True
    assert first.json()['auto_reanalyze_existing'] is False
    second = client.post(path, json=dict(body, command_key='auto-policy-b', reanalyze_existing=True,
        schedule=dict(schedule, time='15:00')))
    assert second.status_code == 200, second.text
    assert client.post(path, json=body).json() == first.json()
    current = client.get(API+'/test_api').json()['profile']
    assert current['schedule']['time'] == '15:00'
    assert current['auto_reanalyze_existing'] is True
    assert client.post(path, json=dict(body, reanalyze_existing=True)).status_code == 409
    with s.engine.connect() as conn:
        assert len(list(conn.execute(select(jobs.c.job_id)))) == 1
    assert s.service.jobs.read(s.actor.data_scope, initial['job_id'])['operation'] == original


def test_new_manual_refresh_accepts_legacy_schedule_identity_but_does_not_apply_it(client, profile_scenario):
    schedule = {'enabled':True,'every_days':2,'time':'09:00','timezone':'Europe/Moscow'}
    response = client.post(API+'/test_api/refresh', json={'command_key':'old-shape-new-command',
        'detail_max':0, 'schedule':schedule, 'reanalyze_existing':True})
    assert response.status_code == 202
    profile = client.get(API+'/test_api').json()['profile']
    assert profile['schedule']['enabled'] is False
    assert profile['auto_reanalyze_existing'] is False
    assert profile['next_refresh_at'] is None
