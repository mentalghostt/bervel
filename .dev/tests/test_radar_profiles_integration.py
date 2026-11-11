"""Profile composition, durable queue dispatch, and source parity through real adapters."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from test_ai_radar_runs import run_scenario
from bervel.modules.ai_tender_radar.infrastructure.runs import runs
from bervel.modules.ai_tender_radar.profile_api import profiles_router


def test_profile_service_is_composed_on_existing_pipeline(run_scenario):
    assert run_scenario.service.run_pipeline.profiles.pipeline is run_scenario.service.run_pipeline


def test_zero_refresh_runs_real_search_and_preserves_same_base(run_scenario):
    s = run_scenario
    pipeline = s.service.run_pipeline
    service = pipeline.profiles
    service.save_keywords(s.actor, 'test_api', 'manual', ['Болт'])
    first = service.refresh(s.actor, 'test_api', 'integrated-first', 0)
    assert s.service.run_once(lane='analysis')
    saved = service.read(s.actor, 'test_api')
    assert saved['run']['data']['state'] == 'complete', saved['run']['data'].get('error')
    assert saved['run']['cards']
    assert all(card['information_state'] == 'primary' for card in saved['run']['cards'])
    assert all(card['metrics']['actuality'] in {'active', 'inactive'} for card in saved['run']['cards'])
    identifiers = {card['candidate_id']: card['analysis_id'] for card in saved['run']['cards']}
    second = service.refresh(s.actor, 'test_api', 'integrated-second', 0)
    assert second['run_id'] == first['run_id']
    assert s.service.run_once(lane='analysis')
    again = service.read(s.actor, 'test_api')['run']
    assert {c['candidate_id']: c['analysis_id'] for c in again['cards']} == identifiers
    assert all(not c.get('details_loaded') for c in again['cards'])


def test_expiry_is_read_only_and_does_not_call_source_or_model(run_scenario):
    s = run_scenario
    pipeline = s.service.run_pipeline
    receipt = pipeline.profiles.refresh(s.actor, 'test_api', 'read-only-primary', 0)
    run_id = receipt['run_id']
    pipeline.profiles.repository.upsert_card(run_id, 'expired', {
        'candidate_id': 'expired', 'state': 'primary', 'information_state': 'primary',
        'brief': {'OrderName': 'Болт', 'IsPlanning': True,
                  'ApplicationDeadline': '2000-01-01T00:00:00Z'},
        'evaluated_at': datetime.now(timezone.utc).isoformat()})
    pipeline.repository.archive_page(run_id, {'phrase': 'Received malformed page', 'page': 0, 'response': {}})
    with s.engine.connect() as conn:
        before = conn.scalar(select(runs.c.data).where(runs.c.run_id == run_id))
    def unexpected(*args, **kwargs):
        raise AssertionError('GET called external dependency')
    s.model.generate = unexpected
    s.service.kontur.search_page = unexpected
    s.service.kontur.fetch_tender = unexpected
    card = pipeline.profiles.read(s.actor, 'test_api')['run']['cards'][0]
    assert card['metrics']['actuality'] == 'inactive'
    assert card['metrics']['inactivity_reason'] == 'deadline_expired'
    with s.engine.connect() as conn:
        assert conn.scalar(select(runs.c.data).where(runs.c.run_id == run_id)) == before


def test_unexpected_refresh_failure_finishes_queue_and_preserves_readable_profile(run_scenario, monkeypatch):
    s = run_scenario
    from bervel.modules.ai_tender_radar import profile_refresh
    def broken(*args):
        raise RuntimeError('Independent source failure')
    monkeypatch.setattr(profile_refresh, 'execute_profile_refresh', broken)
    job = s.service.run_pipeline.profiles.refresh(s.actor, 'test_api', 'unexpected-failure', 0)
    assert s.service.run_once(lane='analysis')
    assert s.service.jobs.read(s.actor.data_scope, job['job_id'])['state'] == 'failed'
    assert s.service.run_pipeline.profiles.read(s.actor, 'test_api')['run']['data']['state'] == 'failed'


def test_profile_tick_is_wired_into_analysis_lane_only():
    from bervel.worker import _run_radar_lanes
    from threading import Event, Lock
    stop, lock = Event(), Lock()
    observed = []
    class Profiles:
        def enqueue_due(self):
            with lock:
                observed.append('tick')
    class Handler:
        run_pipeline = SimpleNamespace(profiles=Profiles())
        def run_once(self, *, lane):
            with lock:
                observed.append(lane)
                if 'analysis' in observed and 'question' in observed:
                    stop.set()
            return True
    _run_radar_lanes(Handler(), stop)
    assert 'tick' in observed
    assert observed.index('tick') < observed.index('analysis')
