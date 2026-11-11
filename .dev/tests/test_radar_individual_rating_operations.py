"""Explicit upgrades keep frozen inputs and never replace the profile owner."""
from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy import select

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.individual_rating import PROMPT_VERSION
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
from bervel.modules.ai_tender_radar.infrastructure.runs import analyses, runs
from test_ai_radar_runs import run_scenario
from test_radar_profiles_api import client, profile_scenario
from test_radar_profile_refresh import no_retry_wait


def test_explicit_failed_profile_upgrade_changes_only_rating_version(client, profile_scenario):
    s = profile_scenario
    first = s.profiles.refresh(s.actor, 'test_api', 'legacy-upgrade-base', 0)
    versions = {'matching': 'historical-match', 'production': 4, 'review': 'historical-review'}
    checkpoint = {'job_id': str(first['job_id']), 'analyzed_ids': ['already-completed'], 'search_done': True}
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == first['job_id']).values(
            state='failed', error={'code': 'MODEL_UNAVAILABLE', 'message': 'failed'}))
        conn.execute(runs.update().where(runs.c.run_id == first['run_id']).values(data=runs.c.data.op('||')({
            'state': 'failed', 'stage_versions': versions, 'refresh_checkpoint': checkpoint})))
    path = '/api/v0.1/ai-tender-radar/profiles/test_api/resume'
    body = {'command_key': 'explicit-local-upgrade', 'rating_version': PROMPT_VERSION}
    response = client.post(path, json=body)
    assert response.status_code == 202, response.text
    assert client.post(path, json=body).json() == response.json()
    assert client.post(path, json={'command_key': body['command_key']}).status_code == 409
    with s.engine.connect() as conn:
        stored = conn.execute(select(runs).where(runs.c.run_id == first['run_id'])).mappings().one()
        upgraded = conn.execute(select(jobs.c.operation).where(jobs.c.job_id == UUID(response.json()['job_id']))).scalar_one()
    assert stored['settings_snapshot_id'] == UUID(s.snapshot['id'])
    assert stored['data']['stage_versions'] == dict(versions, rating=PROMPT_VERSION)
    assert stored['data']['refresh_checkpoint'] == checkpoint
    assert upgraded['refresh_id'] == str(first['job_id'])
    assert upgraded['kind'] == 'profile_refresh'
    assert client.post(path, json={'command_key': 'bad-version', 'rating_version': 'other'}).status_code == 422


@pytest.mark.parametrize('transient_failures', [0, 2])
def test_complete_backfill_scores_saved_cards_without_source_or_reanalysis_and_preserves_owner(profile_scenario, transient_failures):
    s = profile_scenario
    pipeline = s.service.run_pipeline
    s.profiles.save_keywords(s.actor, 'test_api', 'manual', ['болт'])
    first = s.profiles.refresh(s.actor, 'test_api', 'complete-upgrade-base', 1)
    assert s.service.run_once()
    with s.engine.begin() as conn:
        run = conn.execute(select(runs).where(runs.c.run_id == first['run_id'])).mappings().one()
        assert run['data']['state'] == 'complete'
        versions = deepcopy(run['data']['stage_versions']); versions.pop('rating')
        conn.execute(runs.update().where(runs.c.run_id == first['run_id']).values(
            data=dict(run['data'], stage_versions=versions)))
        for row in conn.execute(select(analyses).where(analyses.c.run_id == first['run_id'])).mappings():
            value = {k: v for k, v in row['data'].items() if k not in {
                'individual_rating', 'individual_rating_history', 'individual_rating_completed_refresh_id', 'rating_version'}}
            conn.execute(analyses.update().where(analyses.c.analysis_id == row['analysis_id']).values(data=value))
    before = pipeline.repository.cards(first['run_id'])
    reports = pipeline.repository.read_reports(first['run_id'])
    calls = []

    def score_only(request_id, prompt, **options):
        assert request_id.startswith('card-rating-'), 'Backfill repeated a full analysis stage'
        calls.append(request_id)
        if len(calls) <= transient_failures:
            raise CoreError('MODEL_UNAVAILABLE', 'provider temporarily unavailable')
        return {'answer': '91', 'model': 'same-configured-model'}

    s.model.generate = score_only
    pipeline.sources = {}  # Any source path during backfill would fail.
    receipt = pipeline.operation(s.actor, first['run_id'], 'backfill-local-one', 'rating',
        reference={'rating_version': PROMPT_VERSION})
    assert pipeline.operation(s.actor, first['run_id'], 'backfill-local-one', 'rating',
        reference={'rating_version': PROMPT_VERSION})['job_id'] == receipt['job_id']
    with s.engine.connect() as conn:
        queued = conn.scalar(select(runs.c.data).where(runs.c.run_id == first['run_id']))
    assert queued['current_job_id'] == str(first['job_id'])
    assert queued['individual_rating_job_id'] == str(receipt['job_id'])
    assert s.service.run_once()
    after = pipeline.repository.cards(first['run_id'])
    assert len(calls) == transient_failures + 1
    assert after[0]['data']['individual_rating']['score'] == 91
    assert after[0]['analysis_id'] == before[0]['analysis_id']
    for key in ('tender', 'raw_tender', 'analysis', 'review', 'fulfillment', 'analysis_completed_refresh_id'):
        assert after[0]['data'][key] == before[0]['data'][key]
    assert pipeline.repository.read_reports(first['run_id']) == reports
    with s.engine.connect() as conn:
        done = conn.scalar(select(runs.c.data).where(runs.c.run_id == first['run_id']))
    assert done['current_job_id'] == str(first['job_id'])
    assert 'individual_rating_job_id' not in done
    # Fresh explicit command reuses completed individual results, without model I/O.
    pipeline.operation(s.actor, first['run_id'], 'backfill-local-two', 'rating',
        reference={'rating_version': PROMPT_VERSION})
    assert s.service.run_once()
    assert len(calls) == transient_failures + 1


def test_backfill_rejects_active_profile_instead_of_silently_upgrading(profile_scenario):
    s = profile_scenario
    first = s.profiles.refresh(s.actor, 'test_api', 'active-upgrade-base', 0)
    with pytest.raises(CoreError, match='CONFLICT'):
        s.service.run_pipeline.operation(s.actor, first['run_id'], 'do-not-upgrade-active', 'rating',
            reference={'rating_version': PROMPT_VERSION})


def queued_backfill(s):
    first = s.profiles.refresh(s.actor, 'test_api', 'owner-complete-base', 0)
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == first['job_id']).values(state='loaded'))
        conn.execute(runs.update().where(runs.c.run_id == first['run_id']).values(
            data=runs.c.data.op('||')({'state': 'complete', 'phase': 'complete'})))
    receipt = s.service.run_pipeline.operation(s.actor, first['run_id'], 'owner-local-rating', 'rating',
        reference={'rating_version': PROMPT_VERSION})
    return first, receipt


def test_backfill_owner_is_allowlisted_lease_guarded_and_cas_release_preserves_successor(profile_scenario):
    s = profile_scenario
    first, receipt = queued_backfill(s)
    assert s.service.jobs.claim(lane='analysis')['job_id'] == receipt['job_id']
    repository = s.service.run_pipeline.repository
    with pytest.raises(ValueError):
        s.profiles.repository.execution(repository, s.actor.data_scope, first['run_id'], receipt['job_id'],
            owner_field='settings_snapshot_id')
    owner = s.profiles.repository.execution(repository, s.actor.data_scope, first['run_id'], receipt['job_id'],
        owner_field='individual_rating_job_id')
    owner.upsert_card(first['run_id'], 'durable-score', {'individual_rating': {'status': 'complete', 'score': 72}})
    replacement = '00000000-0000-0000-0000-000000000999'
    with s.engine.begin() as conn:
        conn.execute(runs.update().where(runs.c.run_id == first['run_id']).values(
            data=runs.c.data.op('||')({'individual_rating_job_id': replacement})))
    with pytest.raises(CoreError, match='AI_RADAR_JOB_INTERRUPTED'):
        owner.upsert_card(first['run_id'], 'durable-score', {'individual_rating': {'score': 0}})
    repository.release_rating_owner(s.actor.data_scope, first['run_id'], receipt['job_id'])
    with s.engine.connect() as conn:
        data = conn.scalar(select(runs.c.data).where(runs.c.run_id == first['run_id']))
        card = conn.scalar(select(analyses.c.data).where(analyses.c.run_id == first['run_id']))
    assert data['individual_rating_job_id'] == replacement
    assert data['current_job_id'] == str(first['job_id'])
    assert card['individual_rating']['score'] == 72


def test_stop_targets_backfill_job_while_profile_owner_stays_complete(profile_scenario):
    s = profile_scenario
    first, receipt = queued_backfill(s)
    s.service.run_pipeline.stop(s.actor, first['run_id'])
    with s.engine.connect() as conn:
        state = conn.execute(select(jobs.c.state, jobs.c.error).where(jobs.c.job_id == receipt['job_id'])).mappings().one()
        previous = conn.scalar(select(jobs.c.state).where(jobs.c.job_id == first['job_id']))
    assert state['state'] == 'failed' and state['error']['code'] == 'USER_STOPPED'
    assert previous == 'loaded'


def test_expired_profile_finish_is_fenced_before_touching_run_or_profile(profile_scenario):
    from datetime import datetime, timedelta, timezone
    s = profile_scenario
    first = s.profiles.refresh(s.actor, 'test_api', 'expired-finish', 0)
    s.service.jobs.claim(lane='analysis')
    repository = s.service.run_pipeline.repository
    owner = s.profiles.repository.execution(repository, s.actor.data_scope, first['run_id'], first['job_id'])
    before = repository.read(s.actor.data_scope, first['run_id'])
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == first['job_id']).values(
            lease_until=datetime.now(timezone.utc) - timedelta(seconds=1)))
    with pytest.raises(CoreError, match='AI_RADAR_JOB_INTERRUPTED'):
        owner.finish_refresh(s.actor.data_scope, 'test_api', first['job_id'], error={'code': 'STALE', 'message': 'stale'})
    assert repository.read(s.actor.data_scope, first['run_id']) == before
    assert s.profiles.repository.read(s.actor.data_scope, 'test_api')['data'].get('last_error') is None


def test_failed_backfill_is_visible_and_profile_resume_retries_only_scores(client, profile_scenario):
    s = profile_scenario
    pipeline = s.service.run_pipeline
    s.profiles.save_keywords(s.actor, 'test_api', 'manual', ['болт'])
    first = s.profiles.refresh(s.actor, 'test_api', 'visible-score-base', 1)
    assert s.service.run_once()
    before = pipeline.repository.cards(first['run_id'])[0]
    with s.engine.begin() as conn:
        data = dict(before['data'])
        data.pop('individual_rating')
        conn.execute(analyses.update().where(analyses.c.analysis_id == before['analysis_id']).values(data=data))
    calls = []

    def fail(request_id, *args, **kwargs):
        assert request_id.startswith('card-rating-')
        calls.append(request_id)
        raise CoreError('MODEL_AUTH_ERROR', 'provider authentication failed')

    s.model.generate = fail
    pipeline.sources = {}
    job = pipeline.operation(s.actor, first['run_id'], 'visible-score-failed', 'rating',
        reference={'rating_version': PROMPT_VERSION})
    assert s.service.run_once()
    failed = client.get('/api/v0.1/ai-tender-radar/profiles/test_api').json()['run']
    assert failed['data']['state'] == 'failed'
    assert failed['data']['error']['code'] == 'MODEL_AUTH_ERROR'
    assert failed['cards'][0]['rating'] == {'status': 'failed', 'score': None, 'prompt_version': PROMPT_VERSION}
    s.model.generate = lambda request_id, *args, **kwargs: {'answer': '83', 'model': 'same-model'}
    path = '/api/v0.1/ai-tender-radar/profiles/test_api/resume'
    receipt = client.post(path, json={'command_key': 'visible-score-resume'})
    assert receipt.status_code == 202, receipt.text
    assert client.post(path, json={'command_key': 'visible-score-resume'}).json() == receipt.json()
    assert s.service.run_once()
    after = pipeline.repository.cards(first['run_id'])[0]
    assert after['data']['analysis'] == before['data']['analysis']
    assert after['analysis_id'] == before['analysis_id']
    assert after['data']['analysis_settings_snapshot_id'] == before['data']['analysis_settings_snapshot_id']
    for score in [0, 83, 100]:
        value = dict(after['data']['individual_rating'], score=score)
        s.profiles.repository.upsert_card(first['run_id'], after['candidate_id'], {'individual_rating': value})
        public = client.get('/api/v0.1/ai-tender-radar/profiles/test_api').json()['run']
        assert public['data']['state'] == 'complete'
        assert public['cards'][0]['rating'] == {'status': 'complete', 'score': score, 'prompt_version': PROMPT_VERSION}
        run_public = s.client.get('/api/v0.1/ai-tender-radar/runs/' + str(first['run_id'])).json()
        assert run_public['cards'][0]['rating'] == public['cards'][0]['rating']
