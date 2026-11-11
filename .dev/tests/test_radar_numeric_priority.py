"""One scalar decision per tender, bounded concurrency and durable continuation."""
from copy import deepcopy
import json
from threading import Barrier, Event, Lock

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.brief_ranking import rank_briefs_with_model
from test_radar_profile_refresh import execute, refresh


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setattr('bervel.modules.ai_tender_radar.brief_ranking._wait_for_retry',
                        lambda delay, heartbeat: heartbeat())


def brief(key):
    return {'id': key, 'state': 'candidate', 'brief': {'OrderName': f'Болт М8 {key}'}}


PROFILE = {'products': [{'id': 'bolt', 'name': 'Болт М8', 'enabled': True, 'attributes': {}}]}


def rank(rows, model, **kwargs):
    return rank_briefs_with_model(rows, PROFILE, model, 'refresh', heartbeat=lambda: None, **kwargs)


class NumericModel:
    def __init__(self, answer='75'):
        self.answer, self.calls = answer, []
        self.lock = Lock()

    def generate(self, request_id, prompt, **options):
        payload = json.loads(prompt.partition('\nВХОД\n')[2])
        with self.lock:
            self.calls.append((request_id, payload, options))
        return {'answer': self.answer, 'model': 'numeric-test'}


@pytest.mark.parametrize('answer', ['0', '47', '100', ' 83\n'])
def test_model_returns_only_an_integer_and_each_request_has_one_tender(answer):
    model = NumericModel(answer)
    rows = [brief('a'), brief('b'), brief('a')]
    result = rank(rows, model)
    assert [row['rank'] for row in result] == [int(answer)] * 2
    assert all(row['model_evidence'] == {'score': int(answer)} for row in result)
    assert len(model.calls) == 2
    assert all(len(payload['candidates']) == 1 for _, payload, _ in model.calls)
    assert all(options.get('output_schema') is None and options['web_search'] is False
               for _, _, options in model.calls)
    assert len({request_id for request_id, _, _ in model.calls}) == 2
    assert 'rank' not in rows[0]


@pytest.mark.parametrize('answer', ['-1', '101', '50.0', 'true', '"50"', '{"score":50}',
                                   '50 пояснение', '01', '', None, 50])
def test_non_scalar_or_out_of_range_answers_never_become_priorities(answer):
    with pytest.raises(CoreError) as error:
        rank([brief('a')], NumericModel(answer))
    assert error.value.code == 'MODEL_INVALID_OUTPUT'


def test_exactly_ten_requests_can_run_together_without_a_larger_backlog():
    model = NumericModel()
    generate = model.generate
    gate, lock = Barrier(10, timeout=5), Lock()
    active = maximum = 0

    def concurrent(*args, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            gate.wait()
            return generate(*args, **kwargs)
        finally:
            with lock:
                active -= 1

    model.generate = concurrent
    pending_sizes, completed_counts = [], []
    result = rank([brief(str(i)) for i in range(20)], model,
                  progress=lambda new, pending, completed: (
                      pending_sizes.append(len(pending)), completed_counts.append(completed)))
    assert maximum == 10
    assert max(pending_sizes) == 10
    assert len(result) == len(model.calls) == 20
    assert completed_counts == sorted(completed_counts)
    assert completed_counts[-1] == 20


def test_finished_tenders_are_saved_before_a_slow_first_request_finishes():
    model, release = NumericModel(), Event()
    generate, saved = model.generate, {}

    def delayed(request_id, prompt, **kwargs):
        payload = json.loads(prompt.partition('\nВХОД\n')[2])
        if payload['candidates'][0]['id'] == 'slow':
            assert release.wait(5), 'Completed siblings were held behind the slow request'
        return generate(request_id, prompt, **kwargs)

    def progress(new, pending, completed):
        saved.update(deepcopy(new))
        if len(saved) == 9:
            assert 'slow' not in saved
            release.set()

    model.generate = delayed
    rank([brief('slow'), *[brief(str(i)) for i in range(9)]], model, progress=progress)
    assert len(saved) == 10


def test_an_exhausted_request_does_not_discard_finished_siblings():
    gate = Barrier(10, timeout=5)
    model, saved = NumericModel(), {}
    generate, failed_calls = model.generate, 0

    def unreliable(request_id, prompt, **kwargs):
        nonlocal failed_calls
        payload = json.loads(prompt.partition('\nВХОД\n')[2])
        if payload['candidates'][0]['id'] == 'bad':
            failed_calls += 1
            if failed_calls == 1:
                gate.wait()
            raise CoreError('MODEL_UNAVAILABLE', 'Unavailable')
        gate.wait()
        return generate(request_id, prompt, **kwargs)

    model.generate = unreliable
    with pytest.raises(CoreError) as error:
        rank([brief('bad'), *[brief(str(i)) for i in range(9)]], model,
             progress=lambda new, pending, completed: saved.update(deepcopy(new)))
    assert error.value.code == 'MODEL_UNAVAILABLE'
    assert set(saved) == {str(i) for i in range(9)}
    assert len(model.calls) == 9
    assert failed_calls == 3
    model.generate = generate
    result = rank([brief('bad'), *[brief(str(i)) for i in range(9)]], model,
                  saved_evaluations=saved)
    assert len(result) == 10
    assert len(model.calls) == 10  # Only the failed tender was retried by explicit continuation.


def test_old_scores_and_explanations_are_preserved_without_new_model_calls():
    old = {'id': 'old', 'score': 91, 'relevance': 'strong', 'product_ids': ['bolt'],
           'quote': 'Болт М8', 'reason': 'Сохранённое обоснование'}
    cached = {'old': old, 'zero': {'score': 0}}
    model = NumericModel('80')
    rows = [brief('old'), brief('zero'), brief('new'), dict(brief('closed'), state='closed')]
    result = rank(rows, model, saved_evaluations=cached)
    assert [row['id'] for row in result] == ['old', 'new', 'zero', 'closed']
    assert result[0]['model_evidence'] == old
    assert cached == {'old': old, 'zero': {'score': 0}}
    assert len(model.calls) == 1
    assert model.calls[0][1]['candidates'][0]['id'] == 'new'


@pytest.mark.parametrize('score', [True, -1, 101, '50', None])
def test_invalid_saved_numbers_are_not_silently_reused(score):
    with pytest.raises(CoreError) as error:
        rank([brief('a')], NumericModel(), saved_evaluations={'a': {'score': score}})
    assert error.value.code == 'MODEL_INVALID_OUTPUT'


def test_long_source_ids_produce_bounded_stable_model_request_ids():
    key = 'x' * 128
    rows = [brief(key)]
    first, retry = NumericModel(), NumericModel()
    origin = '61e2b011-d82f-4cdb-bef0-c584bb56890b'
    for model in (first, retry):
        rank_briefs_with_model(rows, PROFILE, model, origin, heartbeat=lambda: None)
    identifier = first.calls[0][0]
    assert len(identifier) <= 128
    assert identifier == retry.calls[0][0]
    assert first.calls[0][1]['candidates'][0]['id'] == key


def test_profile_saves_only_the_number_and_keeps_priority_separate_from_full_analysis(refresh):
    refresh.job['operation']['detail_max'] = 0
    execute(refresh)
    for card in refresh.repository.saved.values():
        assert card['priority_evidence'] == {'score': card['priority_score']}
        assert card['priority_reason'] is None
        assert card['analysis'] is None
    assert refresh.source.fetches == []
    checkpoint = refresh.repository.state['checkpoint']
    assert checkpoint['ranking_completed'] == checkpoint['ranking_total'] == 2
    assert checkpoint['ranking_pending_ids'] == []


def test_card_commit_before_progress_commit_is_recovered_without_repeating_the_decision(refresh):
    class ProcessDeath(BaseException):
        pass

    refresh.job['operation']['detail_max'] = 0
    upsert, crashed = refresh.profiles.upsert_card, False

    def commit_then_die(run_id, key, card):
        nonlocal crashed
        saved = upsert(run_id, key, card)
        if card.get('priority_refresh_id') and not crashed:
            crashed = True
            raise ProcessDeath()
        return saved

    refresh.profiles.upsert_card = commit_then_die
    with pytest.raises(ProcessDeath):
        execute(refresh)
    scores = {key: deepcopy(card['priority_evidence']) for key, card in refresh.repository.saved.items()
              if card.get('priority_refresh_id')}
    assert scores
    previous_ids = list(refresh.pipeline.model.ranking_ids)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.profiles.upsert_card = upsert
    execute(refresh)
    resumed_ids = refresh.pipeline.model.ranking_ids[len(previous_ids):]
    assert not set(scores) & set(resumed_ids)
    assert refresh.repository.state['state'] == 'complete'
    assert all(refresh.repository.saved[key]['priority_evidence'] == score for key, score in scores.items())


def test_stop_drains_and_saves_completed_requests_without_starting_more(refresh):
    refresh.job['operation']['detail_max'] = 0
    refresh.source.briefs = [{'Id': f'c{i}', 'OrderName': 'Болт М8'} for i in range(25)]
    stopped = False
    original = refresh.profiles.upsert_card

    def heartbeat(job_id):
        if stopped:
            raise CoreError('USER_STOPPED', 'Stopped')

    def save_then_stop(run_id, key, card):
        nonlocal stopped
        result = original(run_id, key, card)
        if card.get('priority_refresh_id'):
            stopped = True
        return result

    refresh.pipeline.jobs.heartbeat = heartbeat
    refresh.profiles.upsert_card = save_then_stop
    with pytest.raises(CoreError) as error:
        execute(refresh)
    assert error.value.code == 'USER_STOPPED'
    scored = [card for card in refresh.repository.saved.values() if card.get('priority_refresh_id')]
    assert 0 < len(scored) <= 10
    assert refresh.repository.state['checkpoint']['ranking_completed'] == len(scored)
    assert len([call for call in refresh.pipeline.model.calls if '-rank-' in call]) == len(scored)
    assert refresh.source.fetches == []
