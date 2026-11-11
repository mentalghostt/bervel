"""Permanent regressions from the independent review's five failing reproductions."""
from copy import deepcopy

import pytest

from test_ai_radar_runs import run_scenario, SETTINGS
from test_radar_profile_refresh import refresh, execute, next_refresh
from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
from bervel.modules.ai_tender_radar.profile_policy import card_actuality


def test_question_replay_survives_new_profile_snapshot_but_not_changed_question(run_scenario):
    s = run_scenario
    p = s.service.run_pipeline
    first = p.profiles.refresh(s.actor, 'test_api', 'review-refresh-one', 0)
    card = p.profiles.repository.upsert_card(first['run_id'], 'review-card', {
        'state': 'primary', 'details_loaded': False, 'brief': {'Id': 'review-card', 'OrderName': 'Болт'}})
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == first['job_id']).values(state='loaded'))
    question = p.operation(s.actor, first['run_id'], 'review-question', 'question',
        analysis_id=card['analysis_id'], question='Какой срок?')
    changed = deepcopy(s.snapshot['profile'])
    changed['products'][0]['name'] = 'Ручная новая версия'
    saved = s.client.put(SETTINGS, json={'base_snapshot_id': s.snapshot['id'],
        'profile': changed, 'command_key': 'review-settings-change'})
    assert saved.status_code == 200, saved.text
    p.profiles.refresh(s.actor, 'test_api', 'review-refresh-two', 0)
    replayed = p.operation(s.actor, first['run_id'], 'review-question', 'question',
        analysis_id=card['analysis_id'], question='Какой срок?')
    assert replayed['job_id'] == question['job_id']
    with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
        p.operation(s.actor, first['run_id'], 'review-question', 'question',
            analysis_id=card['analysis_id'], question='Другой вопрос')


@pytest.mark.parametrize('source_error', ['KONTUR_NOT_FOUND', 'KONTUR_TENDER_CLOSED'])
def test_fresh_open_brief_restores_previous_known_unavailable_card(refresh, source_error):
    refresh.source.error_detail = source_error
    execute(refresh)
    assert card_actuality(refresh.repository.saved['b'])['actuality'] == 'inactive'
    refresh.source.error_detail = None
    next_refresh(refresh, detail_max=1)
    refresh.source.briefs[1].update(IsCancelled=False, IsAbandoned=False, PurchaseStatus=1,
        ApplicationDeadline='2099-01-01T00:00:00Z')
    execute(refresh)
    restored = refresh.repository.saved['b']
    assert card_actuality(restored)['actuality'] == 'active'
    assert restored['details_loaded'] is True
