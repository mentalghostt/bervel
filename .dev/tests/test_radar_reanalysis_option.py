"""v3 full-stage scope is a pinned user choice, never a changed-input guess."""
from copy import deepcopy
from uuid import uuid4

import pytest

from test_radar_profile_refresh import refresh, execute, next_refresh, no_retry_wait
from test_named_radar_execution import tender


V3 = 'incremental-profile-v3'


def begin_v3(env, value):
    next_refresh(env, refresh_version=V3, reanalyze_existing=value)


@pytest.mark.parametrize('reanalyze', [False, True])
def test_v3_scope_is_fresh_details_or_all_full_even_unchanged_or_inactive(refresh, monkeypatch, reanalyze):
    refresh.job['operation']['detail_max'] = 2
    reviews = []
    monkeypatch.setattr('bervel.modules.ai_tender_radar.review.review_card',
        lambda card, *a, **k: reviews.append(card['candidate_id']) or {'state': 'complete'})
    execute(refresh)  # Existing v2 operation stays unchanged.
    old = deepcopy(refresh.repository.saved)
    old_calls = list(refresh.pipeline.model.calls)
    refresh.source.briefs += [{'Id': key, 'OrderName': 'Болт М8'} for key in ('c', 'd')]
    refresh.source.details.update({key: tender(key) for key in ('c', 'd')})
    if not reanalyze:
        refresh.stock['rows'][0]['unit_cost'] = '6'  # Changed facts must not select old cards.
    else:
        refresh.source.briefs = [b for b in refresh.source.briefs if b['Id'] != 'a']  # Inactive still selected.
    reviews.clear()
    begin_v3(refresh, reanalyze)
    execute(refresh)
    expected = {'a', 'b', 'c', 'd'} if reanalyze else {'c', 'd'}
    assert set(reviews) == expected
    assert len(reviews) == len(expected)
    checkpoint = refresh.repository.state['refresh_checkpoint']
    assert checkpoint['analysis_total'] == checkpoint['analysis_completed'] == len(expected)
    assert checkpoint['rating_total'] == checkpoint['rating_completed'] == len(expected)
    rating_calls = [call for call in refresh.pipeline.model.calls[len(old_calls):] if call.startswith('card-rating-')]
    assert len(rating_calls) == len(expected)
    assert sorted(refresh.source.fetches) == ['a', 'b', 'c', 'd']
    if not reanalyze:
        for key in ('a', 'b'):
            for field in ('analysis', 'review', 'individual_rating', 'evaluated_at'):
                assert refresh.repository.saved[key][field] == old[key][field]


def test_false_with_zero_new_details_does_not_pick_old_unfinished_or_changed_cards(refresh):
    execute(refresh)
    old = refresh.repository.saved['b']
    old.update(analysis=None, review=None, individual_rating=None)
    refresh.stock['rows'][0]['free_quantity'] = '0'
    refresh.job['operation']['detail_max'] = 0
    calls = list(refresh.pipeline.model.calls)
    begin_v3(refresh, False)
    execute(refresh)
    assert refresh.pipeline.model.calls == calls
    assert refresh.repository.state['refresh_checkpoint']['analysis_total'] == 0
    assert refresh.repository.state['refresh_checkpoint']['rating_total'] == 0
    assert refresh.repository.saved['b']['analysis'] is None


def test_details_marker_is_atomic_with_paid_input_and_resume_uses_the_same_cohort(refresh):
    class ProcessDeath(BaseException):
        pass
    refresh.job['operation'].update(refresh_version=V3, reanalyze_existing=False)
    original = refresh.profiles.upsert_card
    failed = []
    def persisted_then_died(*args):
        card = original(*args)
        if card.get('details_loaded') and not failed:
            failed.append(card['candidate_id'])
            raise ProcessDeath()
        return card
    refresh.profiles.upsert_card = persisted_then_died
    with pytest.raises(ProcessDeath):
        execute(refresh)
    key = failed[0]
    logical_id = str(refresh.job['job_id'])
    assert refresh.repository.saved[key]['details_loaded_refresh_id'] == logical_id
    assert refresh.repository.saved[key]['raw_tender']
    assert refresh.repository.state['pending_detail'] == key
    fetches = list(refresh.source.fetches)
    old = refresh.job
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.job = dict(old, job_id=uuid4(), operation=dict(old['operation'], refresh_id=logical_id,
        resume_of=str(old['job_id'])))
    refresh.profiles.upsert_card = original
    execute(refresh)
    assert refresh.source.fetches == fetches
    assert refresh.repository.saved[key]['individual_rating']['status'] == 'complete'
    assert refresh.repository.state['refresh_checkpoint']['analysis_total'] == 1


def test_true_repeats_review_and_score_but_resume_does_not_repeat_current_completions(refresh, monkeypatch):
    execute(refresh)
    reviews = []
    monkeypatch.setattr('bervel.modules.ai_tender_radar.review.review_card',
        lambda card, *a, **k: reviews.append(card['candidate_id']) or {'state': 'complete'})
    begin_v3(refresh, True)
    execute(refresh)
    assert reviews == ['b', 'a'] or set(reviews) == {'a', 'b'}
    calls = list(refresh.pipeline.model.calls)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    previous = refresh.job
    refresh.job = dict(previous, job_id=uuid4(), operation=dict(previous['operation'],
        refresh_id=str(previous['job_id']), resume_of=str(previous['job_id'])))
    execute(refresh)
    assert refresh.pipeline.model.calls == calls
    assert len(reviews) == 2
