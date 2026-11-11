"""Metrics command is local, pinned, fenced and separate from full refresh state."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
from bervel.modules.ai_tender_radar.infrastructure.runs import runs
from test_radar_profiles_api import client, profile_scenario
from test_ai_radar_runs import run_scenario
from test_radar_profile_refresh import no_retry_wait


def ready(s):
    s.profiles.save_keywords(s.actor, 'test_api', 'manual', ['болт'])
    first = s.profiles.refresh(s.actor, 'test_api', 'metrics-origin-full', 1)
    assert s.service.run_once()
    return first


def test_metrics_only_uses_latest_pinned_snapshot_without_source_or_model(client, profile_scenario):
    s = profile_scenario
    first = ready(s)
    pipeline = s.service.run_pipeline
    before = pipeline.repository.cards(first['run_id'])
    original_run = pipeline.repository.read(s.actor.data_scope, first['run_id'])
    current = s.company.read(s.actor)
    latest = s.company.save(s.actor, current['id'], current['profile'], 'new-head-before-metrics')
    path = '/api/v0.1/ai-tender-radar/profiles/test_api/metrics'
    body = {'command_key': 'metrics-pinned-one'}
    response = client.post(path, json=body)
    assert response.status_code == 202, response.text
    assert client.post(path, json=body).json() == response.json()
    assert response.json()['run_id'] == str(first['run_id'])
    s.company.save(s.actor, latest['id'], latest['profile'], 'new-head-during-metrics')
    pipeline.sources = {}
    s.model.generate = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Metrics must not call any model'))
    assert s.service.run_once()
    after = pipeline.repository.cards(first['run_id'])
    full = next(row for row in after if row['data'].get('analysis'))
    projection = full['data']['metrics_projection']
    assert projection['settings_snapshot_id'] == str(latest['id'])
    assert projection['dependencies_changed'] is False
    original = next(row['data'] for row in before if row['candidate_id'] == full['candidate_id'])
    for field in ('analysis', 'review', 'fulfillment', 'assessment', 'evaluated_at',
                  'analysis_settings_snapshot_id', 'individual_rating', 'priority_evidence'):
        assert full['data'].get(field) == original.get(field)
    updated = pipeline.repository.read(s.actor.data_scope, first['run_id'])
    assert updated['settings_snapshot_id'] == original_run['settings_snapshot_id']
    assert updated['data']['current_job_id'] == original_run['data']['current_job_id']
    assert updated['data']['refresh_checkpoint'] == original_run['data']['refresh_checkpoint']
    public = client.get('/api/v0.1/ai-tender-radar/profiles/test_api').json()['run']
    assert public['data']['state'] == 'complete'
    assert next(c for c in public['cards'] if c.get('analysis'))['rating']['status'] == 'complete'


def test_metrics_endpoint_enforces_scope_and_write_lane(client, profile_scenario):
    s = profile_scenario
    first = ready(s)
    path = '/api/v0.1/ai-tender-radar/profiles/test_api/metrics'
    assert client.post(path, json={'command_key': 'denied'}, headers={'Authorization': 'Bearer ' + 'c' * 32}).status_code == 403
    assert client.post(path, json={'command_key': 'invalid', 'detail_max': 0}).status_code == 422
    accepted = client.post(path, json={'command_key': 'busy-metrics'})
    assert accepted.status_code == 202
    assert client.post(path, json={'command_key': 'another-metrics'}).status_code == 409
    assert client.post('/api/v0.1/ai-tender-radar/profiles/test_api/refresh',
        json={'command_key': 'overlapping-full', 'detail_max': 0}).status_code == 409
    with s.engine.connect() as conn:
        data = conn.scalar(select(runs.c.data).where(runs.c.run_id == first['run_id']))
    assert data['metrics_job_id'] == accepted.json()['job_id']


def test_expired_metrics_recovery_is_pinned_and_stale_owner_cannot_write(profile_scenario):
    s = profile_scenario
    first = ready(s)
    pipeline = s.service.run_pipeline
    receipt = s.profiles.metrics(s.actor, 'test_api', 'recover-local-metrics')
    claimed = s.service.jobs.claim(lane='analysis')
    assert claimed['job_id'] == receipt['job_id']
    owner = s.profiles.repository.execution(pipeline.repository, s.actor.data_scope,
        first['run_id'], receipt['job_id'], owner_field='metrics_job_id')
    before = pipeline.repository.read(s.actor.data_scope, first['run_id'])
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == receipt['job_id']).values(
            lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
    with pytest.raises(CoreError, match='AI_RADAR_JOB_INTERRUPTED'):
        owner.upsert_card(first['run_id'], 'no-stale-write', {'metrics_projection': {}})
    assert s.service.jobs.claim(lane='analysis') is None
    assert s.profiles.recover_interrupted() == 1
    assert s.profiles.recover_interrupted() == 0
    recovered = pipeline.repository.read(s.actor.data_scope, first['run_id'])
    with s.engine.connect() as conn:
        replacement = dict(conn.execute(select(jobs).where(
            jobs.c.job_id == UUID(recovered['data']['metrics_job_id']))).mappings().one())
    assert replacement['operation']['resume_of'] == str(receipt['job_id'])
    assert replacement['operation']['settings_snapshot_id'] == receipt['operation']['settings_snapshot_id']
    assert recovered['data']['current_job_id'] == before['data']['current_job_id']
    pipeline.sources = {}
    s.model.generate = lambda *a, **k: pytest.fail('Metrics recovery cannot call model')
    assert s.service.run_once()


def test_metrics_stop_resume_and_replaced_receipt_are_fenced(client, profile_scenario):
    s = profile_scenario
    first = ready(s)
    pipeline = s.service.run_pipeline
    receipt = s.profiles.metrics(s.actor, 'test_api', 'stop-local-metrics')
    pipeline.stop(s.actor, first['run_id'])
    failed = client.get('/api/v0.1/ai-tender-radar/profiles/test_api').json()['run']
    assert failed['data']['resume_kind'] == 'metrics'
    assert failed['data']['metrics_job']['error']['code'] == 'USER_STOPPED'
    pipeline.sources = {}
    s.model.generate = lambda *a, **k: pytest.fail('Metrics resume cannot call model')
    resumed = s.profiles.resume(s.actor, 'test_api', 'resume-local-metrics')
    assert resumed['operation']['resume_of'] == str(receipt['job_id'])
    assert s.profiles.resume(s.actor, 'test_api', 'resume-local-metrics')['job_id'] == resumed['job_id']
    assert s.service.run_once()
    with pytest.raises(CoreError, match='CONFLICT'):
        s.profiles.repository.resume_metrics(s.service.jobs, s.actor, 'test_api',
            'cannot-resume-old-receipt', receipt)
    public = client.get('/api/v0.1/ai-tender-radar/profiles/test_api').json()['run']
    assert public['data']['state'] == 'complete'
    assert public['data']['metrics_job']['state'] == 'loaded'
    assert public['data']['current_job_id'] == str(first['job_id'])
