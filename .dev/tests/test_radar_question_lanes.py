"""Independent durable question/analysis dispatch, without live model calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from importlib.resources import files
from threading import Barrier, Event, Lock
from time import monotonic
from types import SimpleNamespace
from uuid import UUID, uuid4

from alembic import command as migrations
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef
from bervel.modules.ai_tender_radar.infrastructure.repository import PostgresAiTenderRadarRepository, jobs
from test_ai_radar_runs import API, completed, enqueue, run_scenario


def put(repository, actor, key, kind=None, **extra):
    return repository.enqueue(actor, key, ArtifactRef(UUID(int=1)), 'test', 'a' * 64, None,
                              operation=None if kind is None else {'kind': kind, **extra})


def test_each_scope_accepts_one_job_per_lane_and_keeps_exact_replay(pg_engine):
    repository = PostgresAiTenderRadarRepository(pg_engine)
    actor = ActorContext('owner', 'lane-scope', frozenset())
    analysis = put(repository, actor, 'analysis', 'rating')
    question = put(repository, actor, 'question', 'question', question='Why?')
    assert put(repository, actor, 'question', 'question', question='Why?')['job_id'] == question['job_id']
    assert put(repository, actor, 'analysis', 'rating')['job_id'] == analysis['job_id']
    with pytest.raises(CoreError, match='Ключ команды'):
        put(repository, actor, 'question', 'question', question='Changed')
    for kind in ('question', 'rating', None):
        with pytest.raises(CoreError) as failure:
            put(repository, actor, str(uuid4()), kind)
        assert failure.value.code == 'PROCESS_ACTIVE'
    other = ActorContext('owner', 'other-scope', frozenset())
    put(repository, other, 'question', 'question')
    assert repository.active(actor.data_scope)['job_id'] == analysis['job_id']
    assert repository.claim(lane='question')['job_id'] == question['job_id']
    assert repository.claim(lane='analysis')['job_id'] == analysis['job_id']
    assert repository.claim(lane='analysis') is None


def test_legacy_batch_advances_while_question_is_running(pg_engine):
    repository = PostgresAiTenderRadarRepository(pg_engine)
    actor = ActorContext('owner', 'batch-scope', frozenset())
    first = put(repository, actor, 'legacy')
    second_id = uuid4()
    with pg_engine.begin() as connection:
        connection.execute(jobs.update().where(jobs.c.job_id == first['job_id']).values(batch_id=first['job_id']))
        connection.execute(jobs.insert().values(job_id=second_id, batch_id=first['job_id'],
            data_scope=actor.data_scope, actor_id=actor.actor_id, command_key='batch-second',
            stock_artifact_id=UUID(int=1), stock_sha256='a' * 64, search_phrase='test', state='waiting'))
    question = put(repository, actor, 'question', 'question')
    assert repository.claim(lane='question')['job_id'] == question['job_id']
    assert repository.claim(lane='analysis')['job_id'] == first['job_id']
    repository.fail(first['job_id'], CoreError('MODEL_TIMEOUT', 'Unknown outcome'))
    assert repository.active(actor.data_scope)['job_id'] == second_id
    assert repository.claim()['job_id'] == second_id
    repository.finish_operation(second_id)
    assert repository.active(actor.data_scope) is None
    assert repository.read(actor.data_scope, question['job_id'])['state'] == 'running'


def test_question_runs_before_blocked_analysis_and_never_changes_run_owner(run_scenario):
    s = run_scenario
    receipt, saved = completed(s)
    run_id = receipt['run_id']
    original = deepcopy(s.service.run_pipeline.repository.read(s.actor.data_scope, UUID(run_id))['data'])
    background = enqueue(s, {**__import__('test_ai_radar_runs').command(s, 'parallel-background')})
    entered, release = Event(), Event()
    execute = s.service.run_pipeline.execute
    def blocked(job):
        if str(job['job_id']) == background['job_id']:
            entered.set()
            assert release.wait(10)
            return
        return execute(job)
    s.service.run_pipeline.execute = blocked
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(s.service.run_once, lane='analysis')
        try:
            assert entered.wait(5)
            response = s.client.post(f'{API}/{run_id}/question', json={'command_key': 'parallel-question',
                'analysis_id': saved['cards'][0]['analysis_id'], 'question': 'Почему такой результат?'})
            assert response.status_code == 202, response.text
            assert pool.submit(s.service.run_once, lane='question').result(timeout=5)
            after = s.client.get(f'{API}/{run_id}').json()
            answer = next(r for r in after['reports'] if r['job_id'] == response.json()['job_id'])
            assert answer['result']['state'] == 'complete'
            assert not future.done(), 'The question must finish while the analysis remains blocked'
            assert s.service.run_pipeline.repository.read(s.actor.data_scope, UUID(run_id))['data'] == original
            assert s.client.delete(f'{API}/{background["run_id"]}').status_code == 409
        finally:
            release.set()
        assert future.result(timeout=5)


def test_question_rejects_analyzing_card_and_other_scope(run_scenario):
    from bervel.modules.ai_tender_radar.infrastructure.runs import analyses
    s = run_scenario
    receipt, saved = completed(s)
    card = saved['cards'][0]
    body = {'command_key': 'not-ready', 'analysis_id': card['analysis_id'], 'question': 'Почему?'}
    url = f'{API}/{receipt["run_id"]}/question'
    assert s.client.post(url, json=body, headers={'Authorization': 'Bearer ' + 'b' * 32}).status_code == 404
    assert s.client.post(url, json={**body, 'analysis_id': str(uuid4())}).status_code == 404
    with s.engine.begin() as connection:
        connection.execute(analyses.update().where(analyses.c.analysis_id == UUID(card['analysis_id']))
            .values(data=analyses.c.data.op('||')({'state': 'analyzing'})))
    assert s.client.post(url, json=body).status_code == 409


def test_question_sees_its_saved_comparative_rating_without_changing_feasibility(run_scenario):
    from bervel.modules.ai_tender_radar.comparative_rating import build_snapshot, initial_result
    from bervel.modules.ai_tender_radar.infrastructure.runs import analyses
    s = run_scenario
    receipt, saved = completed(s)
    run_id = UUID(receipt['run_id'])
    card_id = saved['cards'][0]['analysis_id']
    # A saved card without a completed positional analysis still participates in the rating cohort.
    with s.engine.begin() as connection:
        connection.execute(analyses.update().where(analyses.c.analysis_id == UUID(card_id)).values(
            data=analyses.c.data.op('||')({'analysis': None, 'fulfillment': {
                'status': 'infeasible', 'reasons': ['CAPACITY_INSUFFICIENT'], 'production_allocations': []}})))
    before = s.client.get(f'{API}/{run_id}').json()
    snapshot = build_snapshot(before['cards'])
    rating = dict(initial_result(snapshot, 'saved-rating'), status='complete', model='controlled',
                  ratings=[{'id': c['id'], 'score': 92} for c in snapshot['cards']])
    s.service.run_pipeline.repository.report(uuid4(), run_id, {'kind': 'rating', 'result': rating})
    saved_rows = s.service.run_pipeline.repository.cards(run_id)
    seen = []
    class QuestionModel:
        def generate(self, request_id, prompt, **options):
            payload = json.loads(prompt.split('\nВХОД\n', 1)[1])
            seen.append(payload)
            assert payload['card']['rating'] == {'status': 'complete', 'score': 92}
            assert payload['card']['rating_context']['cohort_size'] == len(snapshot['cards'])
            assert payload['card']['metrics'] == next(c for c in before['cards'] if c['analysis_id'] == card_id)['metrics']
            assert payload['calculated']['score'] == 0
            assert payload['calculated']['fulfillment_status'] == 'infeasible'
            assert all(c['analysis_id'] not in prompt for c in before['cards'] if c['analysis_id'] != card_id)
            answer = dict(recommendation='Участие не подтверждено.', answer='Сравнительный балл 92 не подтверждает выполнимость заказа.',
                risks=[], assumptions=[], evidence_refs=['card:/rating', 'card:/fulfillment'],
                calculated=payload['calculated'], decision='decline', proposes_production=False, calculations=[])
            return {'model': 'controlled', 'answer': json.dumps(dict(action='final', ref='', offset=0,
                length=16000, query='', notes='', response=answer), ensure_ascii=False)}
    s.service.run_pipeline.model = QuestionModel()
    response = s.client.post(f'{API}/{run_id}/question', json={'command_key': 'question-rating',
        'analysis_id': card_id, 'question': 'Почему Оценка ИИ 92, хотя исполнить заказ нельзя?'})
    assert response.status_code == 202, response.text
    assert s.service.run_once(lane='question')
    report = next(r['data'] for r in s.service.run_pipeline.repository.read_reports(run_id)
                  if str(r['job_id']) == response.json()['job_id'])
    assert report['result']['state'] == 'complete', report
    assert report['result']['decision'] == 'decline'
    assert len(seen) == 1
    assert s.service.run_pipeline.repository.cards(run_id) == saved_rows


def test_stop_only_analysis_delete_waits_for_question_and_expired_question_is_not_retried(run_scenario):
    s = run_scenario
    receipt, saved = completed(s)
    run_id = receipt['run_id']
    response = s.client.post(f'{API}/{run_id}/question', json={'command_key': 'question-stop',
        'analysis_id': saved['cards'][0]['analysis_id'], 'question': 'Почему?'})
    assert response.status_code == 202
    question = s.service.jobs.claim(lane='question')
    background = enqueue(s, __import__('test_ai_radar_runs').command(s, 'stop-background'))
    assert s.client.post(f'{API}/{background["run_id"]}/stop').status_code == 204
    assert s.service.jobs.read(s.actor.data_scope, question['job_id'])['state'] == 'running'
    assert s.client.delete(f'{API}/{run_id}').status_code == 409
    with s.engine.begin() as connection:
        connection.execute(jobs.update().where(jobs.c.job_id == question['job_id'])
            .values(lease_until=text("now() - interval '1 second'")))
    assert s.service.run_once(lane='question') is False
    assert s.service.jobs.read(s.actor.data_scope, question['job_id'])['state'] == 'failed'
    assert s.service.run_once(lane='question') is False
    assert s.client.delete(f'{API}/{run_id}').status_code == 204


def test_worker_lanes_overlap_drain_before_disposal_and_isolate_storage_failure(monkeypatch):
    import signal
    import bervel.worker as worker
    handlers, calls = {}, []
    analysis_entered, question_done = Event(), Event()
    def install(name, handler):
        previous = handlers.get(name, lambda *_: None)
        handlers[name] = handler
        return previous
    monkeypatch.setattr(worker.signal, 'signal', install)
    attempts = 0
    def run_once(*, lane):
        nonlocal attempts
        if lane == 'analysis':
            attempts += 1
            analysis_entered.set()
            if attempts == 1:
                raise SQLAlchemyError('Temporary analysis storage failure')
            calls.append('unexpected-next-analysis')
            return False
        assert analysis_entered.wait(5)
        calls.append('question-saved')
        handlers[signal.SIGTERM](None, None)
        question_done.set()
        return True
    def dispose():
        assert question_done.is_set()
        calls.append('dispose')
    monkeypatch.setattr(worker, 'build_ai_tender_radar_worker', lambda _: (
        SimpleNamespace(dispose=dispose), SimpleNamespace(run_once=run_once)))
    worker.run_worker(SimpleNamespace(), role='ai_tender_radar')
    assert calls == ['question-saved', 'dispose']


def test_source_refresh_and_search_share_interval_without_holding_network_lock():
    from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient, SIMULATOR_KEY
    entered, lock, stamps = Event(), Lock(), []
    class Response:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _): return self.data
    class Transport:
        def open(self, request, **_):
            with lock:
                stamps.append(monotonic())
                first = len(stamps) == 1
            if first:
                assert entered.wait(2), 'HTTP must not hold the rate reservation lock'
            else:
                entered.set()
            return Response(b'plain document' if '/documents/' in request.full_url else b'{}')
    client = KonturClient(SIMULATOR_KEY, base_url='http://127.0.0.1:8000', opener=Transport(), min_interval=.08)
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(client._request, '/external/v1/search')
        document = pool.submit(client.read_documents, {'Docs': [{'Url': 'http://127.0.0.1:8000/documents/one'}]})
        assert request.result(timeout=3) == {}
        assert document.result(timeout=3)[0]['text'] == 'plain document'
    assert len(stamps) == 2 and stamps[1] - stamps[0] >= .07


def test_composition_legacy_analysis_and_question_share_real_origin_interval():
    from bervel.ai_tender_radar_setup import build_ai_tender_radar
    pipeline = build_ai_tender_radar(SimpleNamespace(), SimpleNamespace(), 'test-key', SimpleNamespace())
    legacy, question = pipeline.kontur, pipeline.run_pipeline.sources['kontur_api']
    assert legacy is not question
    assert legacy.allow_configured_api is False
    assert question.allow_configured_api is True
    barrier, lock, stamps = Barrier(2), Lock(), []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self, _): return b'{}'
    class Transport:
        def open(self, request, **_):
            with lock:
                stamps.append(monotonic())
            barrier.wait(timeout=2)  # HTTP must remain concurrent after reservation.
            return Response()
    for client in (legacy, question):
        client.min_interval = .2
        client.opener = Transport()
    with ThreadPoolExecutor(max_workers=2) as pool:
        analysis = pool.submit(legacy._request, '/external/v1/search')
        refresh = pool.submit(question._request, '/external/v1/purchases/test')
        assert analysis.result(timeout=3) == refresh.result(timeout=3) == {}
    assert stamps[1] - stamps[0] >= .18


def test_composition_keeps_simulator_and_real_origin_rate_state_separate():
    from bervel.ai_tender_radar_setup import build_ai_tender_radar
    pipeline = build_ai_tender_radar(SimpleNamespace(), SimpleNamespace(), 'test-key', SimpleNamespace(),
                                   kontur_base_url='http://127.0.0.1:8000')
    simulator, real = pipeline.kontur, pipeline.run_pipeline.sources['kontur_api']
    assert simulator.simulated is True and real.simulated is False
    # A real-origin reservation must not delay the simulator.
    real._reserve_request()
    real.min_interval = 1
    started = monotonic()
    simulator._reserve_request()
    assert monotonic() - started < .15


def migration_config():
    config = Config()
    config.set_main_option('script_location', str(files('bervel.core').joinpath('migrations')))
    return config


def test_radar_branch_and_explicit_target_do_not_install_inspection(pg_engine):
    config = migration_config()
    scripts = ScriptDirectory.from_config(config)
    assert set(scripts.get_heads()) == {'0027b_radar_profiles', '0028_inspection_followups'}
    assert scripts.get_revision('0028_inspection_followups').down_revision == '0027a_radar_question_lane'
    assert scripts.get_revision('0027b_radar_profiles').down_revision == '0027a_radar_question_lane'
    with pg_engine.begin() as connection:
        config.attributes['connection'] = connection
        # Only this isolated fixture schema is changed; production is never used.
        migrations.downgrade(config, '0027_radar_legacy_history')
        migrations.upgrade(config, '0027a_radar_question_lane')
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0027a_radar_question_lane'
        columns = connection.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='chat_turns'")).scalars().all()
        assert not {'followup_of', 'delivery_state'} & set(columns)
        definition = connection.execute(text("SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() AND indexname='ai_tender_radar_one_active_job'")).scalar_one()
        assert 'CASE' in definition and 'question' in definition
        migrations.upgrade(config, '0027b_radar_profiles')
        assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == '0027b_radar_profiles'
        columns = connection.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='chat_turns'")).scalars().all()
        assert not {'followup_of', 'delivery_state'} & set(columns)
        migrations.downgrade(config, '0027_radar_legacy_history')
        definition = connection.execute(text("SELECT indexdef FROM pg_indexes WHERE schemaname=current_schema() AND indexname='ai_tender_radar_one_active_job'")).scalar_one()
        assert 'CASE' not in definition and '(data_scope)' in definition
