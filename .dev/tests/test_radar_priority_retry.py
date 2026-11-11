"""Retry only unfinished primary priorities, after the fresh pass and within a durable budget."""
from collections import Counter
from copy import deepcopy
import json

import pytest
from sqlalchemy import select

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar import brief_ranking as module
from test_radar_numeric_priority import NumericModel, brief, rank
from test_radar_profile_refresh import execute, refresh
from test_radar_profiles_storage import profile_scenario
from test_ai_radar_runs import run_scenario


REAL_WAIT_FOR_RETRY = module._wait_for_retry


@pytest.fixture(autouse=True)
def no_wall_clock_retry_delays(monkeypatch):
    pauses = []
    clock = [1000.0]
    monkeypatch.setattr(module, 'time', lambda: clock[0], raising=False)
    def wait(delay, heartbeat):
        heartbeat()
        pauses.append(delay)
        clock[0] += delay
    monkeypatch.setattr(module, '_wait_for_retry', wait, raising=False)
    return pauses


def flaky_model(failures):
    model, attempts, order = NumericModel('77'), Counter(), []
    original = model.generate

    def generate(request_id, prompt, **options):
        key = json.loads(prompt.partition('\nВХОД\n')[2])['candidates'][0]['id']
        attempts[key] += 1
        order.append(key)
        if key in failures and attempts[key] <= failures[key]:
            raise CoreError('MODEL_TIMEOUT', 'Temporary model timeout')
        return original(request_id, prompt, **options)

    model.generate = generate
    return model, attempts, order


def test_failure_is_deferred_until_every_fresh_tender_has_been_processed(no_wall_clock_retry_delays):
    model, attempts, order = flaky_model({'bad': 1})
    saved, ledger = {}, {}
    rows = [brief('bad'), *[brief(str(i)) for i in range(24)]]
    result = rank(rows, model, attempts=ledger, attempt_progress=lambda key, n: ledger.update({key: n}),
                  progress=lambda new, pending, completed: saved.update(deepcopy(new)))
    assert len(result) == len(saved) == 25
    assert order.index('bad', 1) == 25
    assert attempts == Counter({'bad': 2, **{str(i): 1 for i in range(24)}})
    assert ledger == dict(attempts)
    assert no_wall_clock_retry_delays == [5]


def test_second_retry_is_the_last_and_never_discards_other_successes(no_wall_clock_retry_delays):
    model, attempts, order = flaky_model({'bad': 99})
    saved, ledger = {}, {}
    with pytest.raises(CoreError) as error:
        rank([brief('bad'), brief('good')], model, attempts=ledger,
             attempt_progress=lambda key, n: ledger.update({key: n}),
             progress=lambda new, pending, completed: saved.update(deepcopy(new)))
    assert error.value.code == 'MODEL_TIMEOUT'
    assert '3' in error.value.message
    assert attempts == Counter({'bad': 3, 'good': 1})
    assert saved == {'good': {'score': 77}}
    assert ledger == dict(attempts)
    assert no_wall_clock_retry_delays == [5, 15]


def test_recovery_uses_the_persisted_budget_instead_of_starting_three_more_attempts():
    model, attempts, order = flaky_model({'bad': 99})
    ledger = {'bad': 2}
    with pytest.raises(CoreError):
        rank([brief('bad')], model, attempts=ledger,
             attempt_progress=lambda key, n: ledger.update({key: n}))
    assert attempts['bad'] == 1
    assert ledger['bad'] == 3
    with pytest.raises(CoreError) as error:
        rank([brief('bad')], model, attempts=ledger)
    assert error.value.code == 'PRIMARY_PRIORITY_RETRIES_EXHAUSTED'
    assert attempts['bad'] == 1


@pytest.mark.parametrize('code', ['USER_STOPPED', 'AI_RADAR_JOB_INTERRUPTED', 'MODEL_AUTH_ERROR',
                                  'MODEL_QUOTA_EXCEEDED', 'MODEL_TLS_ERROR', 'MODEL_UNCONFIGURED'])
def test_stop_ownership_and_configuration_failures_are_not_automatically_retried(code, no_wall_clock_retry_delays):
    calls = []
    class Unavailable:
        def generate(self, request_id, prompt, **options):
            calls.append(request_id)
            raise CoreError(code, code)
    with pytest.raises(CoreError) as error:
        rank([brief('bad')], Unavailable())
    assert error.value.code == code
    assert len(calls) == 1
    assert no_wall_clock_retry_delays == []


def test_server_rate_limit_cooldown_is_not_shortened(no_wall_clock_retry_delays):
    model, attempts, order = flaky_model({})
    original = model.generate
    calls = 0
    def rate_limited(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = CoreError('MODEL_RATE_LIMITED', 'Cooldown')
            error.retry_after_seconds = 23
            raise error
        return original(*args, **kwargs)
    model.generate = rate_limited
    rank([brief('bad')], model)
    assert no_wall_clock_retry_delays == [23]


def test_a_corrected_numeric_answer_is_retried_without_creating_a_fake_zero():
    model = NumericModel()
    original, saved, calls = model.generate, {}, 0
    def malformed_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {'model': 'test', 'answer': 'priority is 90'}
        return original(*args, **kwargs)
    model.generate = malformed_once
    result = rank([brief('bad')], model,
                  progress=lambda new, pending, completed: saved.update(deepcopy(new)))
    assert calls == 2
    assert result[0]['rank'] == 75
    assert saved == {'bad': {'score': 75}}


def test_profile_reserves_each_attempt_before_model_and_preserves_every_success(refresh):
    refresh.job['operation']['detail_max'] = 0
    original, seen = refresh.pipeline.model.generate, Counter()
    def temporary_timeout(request_id, prompt, **options):
        if '-rank-' in request_id:
            key = json.loads(prompt.partition('\nВХОД\n')[2])['candidates'][0]['id']
            seen[key] += 1
            ledger = refresh.repository.state['checkpoint']['ranking_retry']
            assert ledger['attempts'][key] == seen[key]
            if key == 'a' and seen[key] == 1:
                raise CoreError('MODEL_TIMEOUT', 'Temporary timeout')
        return original(request_id, prompt, **options)
    refresh.pipeline.model.generate = temporary_timeout
    execute(refresh)
    checkpoint = refresh.repository.state['checkpoint']
    assert refresh.repository.state['state'] == 'complete'
    assert checkpoint['ranking_completed'] == 2
    assert checkpoint['ranking_retry']['attempts'] == {'a': 2, 'b': 1}
    assert refresh.source.fetches == []
    assert len(refresh.source.searches) == 1


def test_profile_crash_does_not_reset_the_retry_budget(refresh):
    class ProcessDeath(BaseException):
        pass
    refresh.job['operation']['detail_max'] = 0
    refresh.source.briefs = [{'Id': 'a', 'OrderName': 'Болт М8'}]
    original, calls = refresh.pipeline.model.generate, 0
    def interrupted(request_id, *args, **kwargs):
        nonlocal calls
        if '-rank-' in request_id:
            calls += 1
            if calls == 1:
                raise CoreError('MODEL_TIMEOUT', 'Timeout')
            raise ProcessDeath()
        return original(request_id, *args, **kwargs)
    refresh.pipeline.model.generate = interrupted
    with pytest.raises(ProcessDeath):
        execute(refresh)
    assert calls == 2
    assert refresh.repository.state['checkpoint']['ranking_retry']['attempts']['a'] == 2
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.pipeline.model.generate = original
    execute(refresh)
    assert refresh.repository.state['checkpoint']['ranking_retry']['attempts']['a'] == 3
    assert len(refresh.source.searches) == 1
    assert refresh.repository.state['state'] == 'complete'


@pytest.mark.parametrize('automatic', [False, True])
def test_explicit_resume_renews_retry_cycle_but_lease_recovery_keeps_it(profile_scenario, automatic):
    from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
    from bervel.modules.ai_tender_radar.infrastructure.runs import runs
    s = profile_scenario
    first = s.profiles.refresh(s.actor, 'test_api', 'retry-cycle-first', 0)
    cycle = str(first['job_id'])
    code = 'AI_RADAR_JOB_INTERRUPTED' if automatic else 'MODEL_TIMEOUT'
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == first['job_id']).values(
            state='failed', lease_until=None, error={'code': code, 'message': code}))
        run = conn.execute(select(runs.c.data).where(runs.c.run_id == first['run_id'])).scalar_one()
        data = dict(run, refresh_checkpoint={'job_id': cycle,
            'ranking_retry': {'cycle_id': cycle, 'attempts': {'a': 3}}})
        conn.execute(runs.update().where(runs.c.run_id == first['run_id']).values(data=data))
    resumed = s.profiles.resume(s.actor, 'test_api', 'retry-cycle-resume',
                               expected_job_id=first['job_id'] if automatic else None)
    new_cycle = resumed['operation']['priority_retry_cycle']
    assert (new_cycle == cycle) is automatic
    assert resumed['operation']['refresh_id'] == cycle
    assert resumed['run_id'] == first['run_id']
    replay = s.profiles.resume(s.actor, 'test_api', 'retry-cycle-resume',
                              expected_job_id=first['job_id'] if automatic else None)
    assert replay['operation']['priority_retry_cycle'] == new_cycle
    assert replay['job_id'] == resumed['job_id']


def test_retry_wait_keeps_lease_alive_and_remains_interruptible(monkeypatch):
    clock, sleeps, beats = [0.0], [], []
    monkeypatch.setattr(module, 'monotonic', lambda: clock[0])
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(module, 'sleep', sleep)
    REAL_WAIT_FOR_RETRY(2.5, lambda: beats.append(clock[0]))
    assert sleeps == [1, 1, .5]
    assert len(beats) == 4
    def stop():
        raise CoreError('USER_STOPPED', 'Stop')
    with pytest.raises(CoreError) as error:
        REAL_WAIT_FOR_RETRY(15, stop)
    assert error.value.code == 'USER_STOPPED'
    assert sleeps == [1, 1, .5]


def test_rate_limit_cooldown_applies_to_fresh_tenders_too(monkeypatch):
    monkeypatch.setattr(module, 'MAX_PARALLEL', 1)
    calls, bad_calls, start = [], 0, module.time()
    class RateLimited:
        def generate(self, request_id, prompt, **options):
            nonlocal bad_calls
            key = json.loads(prompt.partition('\nВХОД\n')[2])['candidates'][0]['id']
            calls.append((key, module.time()))
            if key == 'bad':
                bad_calls += 1
                if bad_calls == 1:
                    error = CoreError('MODEL_RATE_LIMITED', 'Cooldown')
                    error.retry_after_seconds = 23
                    raise error
            return {'model': 'test', 'answer': '77'}
    rank([brief('bad'), brief('fresh')], RateLimited())
    assert next(t for key, t in calls if key == 'fresh') >= start + 23


def test_recovery_honors_a_saved_absolute_cooldown():
    start, model = module.time(), NumericModel()
    deadline = start + 30
    original = model.generate
    def generate(*args, **kwargs):
        assert module.time() >= deadline
        return original(*args, **kwargs)
    model.generate = generate
    rank([brief('a')], model, not_before=deadline)
    assert len(model.calls) == 1


def test_explicit_new_cycle_keeps_unexpired_provider_cooldown(refresh):
    refresh.job['operation']['detail_max'] = 0
    refresh.source.briefs = [{'Id': 'a', 'OrderName': 'Болт М8'}]
    original = refresh.pipeline.model.generate
    def rate_limited(*args, **kwargs):
        error = CoreError('MODEL_RATE_LIMITED', 'Cooldown')
        error.retry_after_seconds = 30
        raise error
    refresh.pipeline.model.generate = rate_limited
    with pytest.raises(CoreError):
        execute(refresh)
    deadline = refresh.repository.state['checkpoint']['ranking_retry']['not_before']
    assert deadline > module.time()
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.job['operation']['priority_retry_cycle'] = 'explicit-next-cycle'
    def resumed(*args, **kwargs):
        assert module.time() >= deadline
        return original(*args, **kwargs)
    refresh.pipeline.model.generate = resumed
    execute(refresh)
    assert refresh.repository.state['state'] == 'complete'
    assert refresh.repository.state['checkpoint']['ranking_retry']['attempts'] == {'a': 1}


def test_terminal_error_is_taken_from_an_unfinished_tender(monkeypatch):
    monkeypatch.setattr(module, 'MAX_PARALLEL', 1)
    calls = Counter()
    class MixedFailures:
        def generate(self, request_id, prompt, **options):
            key = json.loads(prompt.partition('\nВХОД\n')[2])['candidates'][0]['id']
            calls[key] += 1
            if key == 'a':
                raise CoreError('MODEL_TIMEOUT', 'Unfinished A')
            if calls[key] == 1:
                raise CoreError('MODEL_INVALID_OUTPUT', 'Previously failed B')
            return {'model': 'test', 'answer': '77'}
    with pytest.raises(CoreError) as error:
        rank([brief('a'), brief('b')], MixedFailures())
    assert error.value.code == 'MODEL_TIMEOUT'
