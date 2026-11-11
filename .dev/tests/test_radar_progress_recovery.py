"""Process-death regressions, with real calculations and isolated PostgreSQL ownership."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

import pytest
from sqlalchemy import select, text

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
from bervel.modules.ai_tender_radar.infrastructure.runs import analyses, pages, reports, runs
from test_ai_radar_runs import run_scenario
from test_radar_profile_refresh import refresh, execute
from test_radar_profiles_storage import profile_scenario


class ProcessDeath(BaseException):
    pass


def test_primary_decisions_survive_death_and_do_not_repeat_completed_cards(refresh):
    s = refresh
    s.job['operation']['detail_max'] = 0
    s.source.briefs = [{'Id': f'card-{n:03}', 'OrderName': 'Болт М8'} for n in range(70)]
    generate = s.pipeline.model.generate
    def interrupted(request_id, *args, **kwargs):
        if '-rank-' in request_id and json.loads(args[0].partition('\nВХОД\n')[2])['candidates'][0]['id'] == 'card-032':
            raise ProcessDeath()
        return generate(request_id, *args, **kwargs)
    s.pipeline.model.generate = interrupted
    with pytest.raises(ProcessDeath):
        execute(s)
    scored = [c for c in s.repository.saved.values() if c.get('priority_refresh_id') == str(s.job['job_id'])]
    assert 0 < len(scored) < 70
    assert s.repository.state['checkpoint']['ranking_completed'] == len(scored)
    assert s.repository.state['checkpoint']['ranking_total'] == 70
    s.run['data'] = deepcopy(s.repository.state)
    s.pipeline.model.generate = generate
    calls = list(s.pipeline.model.ranking_ids)
    execute(s)
    resumed_ids = s.pipeline.model.ranking_ids[len(calls):]
    assert not {card['candidate_id'] for card in scored} & set(resumed_ids)
    assert s.repository.state['checkpoint']['ranking_completed'] == 70
    assert s.repository.state['state'] == 'complete'


def test_full_card_commit_before_checkpoint_is_not_analyzed_twice(refresh):
    s = refresh
    original = s.profiles.upsert_card
    crashed = False
    def persist_then_die(run_id, key, data):
        nonlocal crashed
        result = original(run_id, key, data)
        if data.get('analysis_completed_refresh_id') and not crashed:
            crashed = True
            raise ProcessDeath()
        return result
    s.profiles.upsert_card = persist_then_die
    with pytest.raises(ProcessDeath):
        execute(s)
    before = list(s.pipeline.model.calls)
    assert s.repository.saved['b']['analysis_completed_refresh_id'] == str(s.job['job_id'])
    s.run['data'] = deepcopy(s.repository.state)
    s.profiles.upsert_card = original
    execute(s)
    assert [call for call in s.pipeline.model.calls if not call.startswith('card-rating-')] == before
    assert s.repository.saved['b']['individual_rating']['score'] == 83
    assert s.repository.state['checkpoint']['analysis_completed'] == 1


def test_received_search_page_recovers_without_another_paid_http(refresh):
    s = refresh
    s.job['operation']['detail_max'] = 0
    archive = s.repository.archive_page
    def archive_then_die(*args):
        archive(*args)
        raise ProcessDeath()
    s.repository.archive_page = archive_then_die
    with pytest.raises(ProcessDeath):
        execute(s)
    s.run['data'] = deepcopy(s.repository.state)
    s.repository.archive_page = archive
    # Fake repository stores the exact archived page; expose the read port.
    s.repository.source_pages = lambda run_id: [{'data': deepcopy(p)} for p in s.repository.pages]
    execute(s)
    assert len(s.source.searches) == 1
    assert len(s.repository.saved) == 2


def test_recovery_uses_new_owner_preserves_checkpoint_and_fences_old_writes(profile_scenario):
    s = profile_scenario
    service = s.profiles
    old = service.refresh(s.actor, 'test_api', 'recovery-origin', 0)
    old_claim = s.service.jobs.claim(lane='analysis')
    assert old_claim['job_id'] == old['job_id']
    original_run = s.service.run_pipeline.repository.read(s.actor.data_scope, old['run_id'])
    checkpoint = {'job_id': str(old['job_id']), 'search_done': True, 'page': 1,
                  'search_attempts': 1, 'seen_ids': ['saved'], 'ranking_completed': 1}
    state = dict(original_run['data'], state='running', phase='ranking',
                 checkpoint=checkpoint, refresh_checkpoint=checkpoint)
    s.service.run_pipeline.repository.update(s.actor.data_scope, old['run_id'], state)
    owner = service.repository.execution(s.service.run_pipeline.repository,
                                        s.actor.data_scope, old['run_id'], old['job_id'])
    owner.upsert_card(old['run_id'], 'saved', {'state': 'primary', 'priority_score': 60})
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == old['job_id']).values(
            state='failed', lease_until=None, error={'code':'AI_RADAR_JOB_INTERRUPTED','message':'process died'}))
    new = service.resume(s.actor, 'test_api', 'recover-first')
    assert new['job_id'] != old['job_id'] and new['run_id'] == old['run_id']
    assert new['operation']['refresh_id'] == str(old['job_id'])
    resumed = s.service.run_pipeline.repository.read(s.actor.data_scope, old['run_id'])
    assert resumed['data']['refresh_checkpoint'] == checkpoint
    assert service.resume(s.actor, 'test_api', 'recover-first')['job_id'] == new['job_id']
    writes = [lambda: owner.update(s.actor.data_scope, old['run_id'], state),
              lambda: owner.upsert_card(old['run_id'], 'saved', {'priority_score':0}),
              lambda: owner.archive_page(old['run_id'], {'response':{}}),
              lambda: owner.prepare_card(old['run_id'], 'saved', {'title':'stale'}),
              lambda: owner.report(old['job_id'], old['run_id'], {'kind':'rating'}),
              lambda: owner.reserve_rating(old['job_id'], old['run_id'], {'status':'running'})]
    for write in writes:
        with pytest.raises(CoreError, match='AI_RADAR_JOB_INTERRUPTED'):
            write()
    with s.engine.connect() as conn:
        assert conn.scalar(select(analyses.c.data).where(analyses.c.run_id == old['run_id']))['priority_score'] == 60
        assert conn.scalar(select(text('count(*)')).select_from(pages)) == 0
        assert conn.scalar(select(text('count(*)')).select_from(reports)) == 0


def test_worker_recovers_expired_profile_once_but_never_user_stop(profile_scenario):
    s = profile_scenario
    old = s.profiles.refresh(s.actor, 'test_api', 'auto-recover-origin', 0)
    s.service.jobs.claim(lane='analysis')
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == old['job_id']).values(
            lease_until=datetime.now(timezone.utc)-timedelta(seconds=2)))
    assert s.service.jobs.claim(lane='analysis') is None
    assert s.profiles.recover_interrupted() == 1
    assert s.profiles.recover_interrupted() == 0
    current = s.service.run_pipeline.repository.read(s.actor.data_scope, old['run_id'])
    new_id = UUID(current['data']['current_job_id'])
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == new_id).values(
            state='failed', lease_until=None, error={'code':'USER_STOPPED','message':'stop'}))
    assert s.profiles.recover_interrupted() == 0


def test_final_rating_resumes_only_unfinished_batch_on_original_snapshot():
    from bervel.modules.ai_tender_radar import comparative_rating as module
    from test_radar_comparative_rating import card, Model
    saved = []
    snapshot = module.build_snapshot([card(n) for n in range(230)])
    model = Model(saved)
    generate = model.generate
    def interrupted(request_id, *args, **kwargs):
        if request_id.endswith('-rating-1'):
            raise ProcessDeath()
        return generate(request_id, *args, **kwargs)
    model.generate = interrupted
    with pytest.raises(ProcessDeath):
        module.rate_snapshot(snapshot, model, 'origin', heartbeat=lambda: None,
                             save=lambda v: saved.append(deepcopy(v)))
    checkpoint = deepcopy(saved[-1])
    assert len(checkpoint['ratings']) == 100
    assert checkpoint['snapshot'] == snapshot
    model.generate = generate
    result = module.rate_snapshot(snapshot, model, 'origin', checkpoint=checkpoint,
        heartbeat=lambda: None, save=lambda v: saved.append(deepcopy(v)))
    assert result['status'] == 'complete'
    assert len(result['ratings']) == 230
    assert len(model.calls) == 3
