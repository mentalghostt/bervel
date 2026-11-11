from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, RLock, get_ident

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.documents import _term
from bervel.modules.ai_tender_radar.named_runs import _CountedModel


def test_four_card_workers_send_concurrently_and_persist_each_attempt_before_sending():
    entered, release = Event(), Event()
    class Runner:
        def __init__(self):
            self.lock, self.calls = Lock(), []

        def generate(self, request_id, prompt):
            with self.lock:
                assert get_ident() in saved_threads
                assert len(checkpoints) >= len(self.calls) + 1
                self.calls.append(request_id)
                if len(self.calls) == 4:
                    entered.set()
            assert release.wait(5)
            return request_id

    runner, state, checkpoints, saved_threads = Runner(), {}, [], set()
    def save():
        checkpoints.append(state['model_attempts'])
        saved_threads.add(get_ident())
    model = _CountedModel(runner, state, RLock(), save)
    with ThreadPoolExecutor(max_workers=4) as workers:
        pending = [workers.submit(model.generate, str(n), 'test') for n in range(4)]
        try:
            assert entered.wait(2), 'All four workers must reach the model before any is released'
            assert all(not future.done() for future in pending)
        finally:
            release.set()
        assert [future.result(timeout=3) for future in pending] == [str(n) for n in range(4)]
    assert checkpoints == list(range(1, 5))
    assert state['model_attempts'] == 4


def test_question_model_call_finishes_while_analysis_model_call_is_held():
    entered, release, lock = Event(), Event(), Lock()
    active = {'analysis': 0, 'question': 0}
    maximum = {'analysis': 0, 'total': 0}
    class Runner:
        def generate(self, request_id, prompt):
            lane = 'question' if request_id == 'question' else 'analysis'
            with lock:
                active[lane] += 1
                maximum['analysis'] = max(maximum['analysis'], active['analysis'])
                maximum['total'] = max(maximum['total'], sum(active.values()))
                if active['analysis'] == 3:
                    entered.set()
            try:
                if lane == 'analysis':
                    assert release.wait(5)
                return request_id
            finally:
                with lock:
                    active[lane] -= 1
    runner = Runner()
    analysis = _CountedModel(runner, {}, RLock(), lambda: None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        held = [pool.submit(analysis.generate, str(index), 'analysis') for index in range(3)]
        try:
            assert entered.wait(3)
            assert pool.submit(runner.generate, 'question', 'question').result(timeout=3) == 'question'
            assert not held[0].done()
            assert maximum == {'analysis': 3, 'total': 4}
        finally:
            release.set()
        assert [future.result(timeout=3) for future in held] == ['0', '1', '2']


@pytest.mark.parametrize('code', ['MODEL_RATE_LIMITED', 'MODEL_TIMEOUT', 'MODEL_UNAVAILABLE'])
def test_counted_model_preserves_failed_attempt_without_repeating(code):
    state, checkpoints, calls = {}, [], []
    failure = CoreError(code, 'Provider failure or unknown outcome')
    class Runner:
        def generate(self, *args, **kwargs):
            assert checkpoints == [1]
            calls.append((args, kwargs))
            raise failure
    model = _CountedModel(Runner(), state, RLock(), lambda: checkpoints.append(state['model_attempts']))
    with pytest.raises(CoreError) as caught:
        model.generate('same-id', 'same-prompt', timeout_ms=1000)
    assert caught.value is failure
    assert calls == [(('same-id', 'same-prompt'), {'timeout_ms': 1000})]
    assert state['model_attempts'] == 1


def test_counted_model_does_not_send_if_attempt_cannot_be_saved():
    class Runner:
        def generate(self, *args, **kwargs):
            pytest.fail('Must save the attempt before sending')
    def save():
        raise RuntimeError('Storage unavailable')
    model = _CountedModel(Runner(), {}, RLock(), save)
    with pytest.raises(RuntimeError, match='Storage unavailable'):
        model.generate('id', 'prompt')


@pytest.mark.parametrize('quote', [
    'Срок доставки, дней: 2',
    'Срок доставки (календарных дней): 2.',
    'Доставка, дн. — 2',
    'Перевозка занимает 2 календарных дня.',
])
def test_delivery_days_accepts_a_source_label_before_its_value(quote):
    fact = {'value': 2, 'document': 0, 'quote': quote}
    assert _term('delivery_days', fact, [{'text': quote}], None, []) == 2


@pytest.mark.parametrize('quote', [
    'Срок доставки, рабочих дней: 2',
    'Срок доставки, дней: от 2 до 3',
    'Срок доставки, дней: 2–3',
    'Срок доставки, дней: 3',
    'Доставка: 2 коробки',
    'Срок доставки не указан; срок хранения, дней: 2',
    'Срок доставки, дней: 2; альтернативный срок доставки 3 дня.',
])
def test_delivery_days_does_not_accept_other_units_ranges_or_conflicting_numbers(quote):
    with pytest.raises(CoreError, match='Срок доставки'):
        _term('delivery_days', {'value': 2, 'document': 0, 'quote': quote}, [{'text': quote}], None, [])
