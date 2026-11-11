from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact
from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient
from bervel.modules.ai_tender_radar.infrastructure.runs import PostgresRunRepository
from bervel.modules.ai_tender_radar.named_runs import execute_named
from test_ai_radar_runs import run_scenario, API
from test_named_radar_execution import environment, execute, Source, tender


def test_file_uuid_json_enqueues_and_executes_real_http_pg(run_scenario):
    s = run_scenario
    content = json.dumps({'Id':'file-review','Title':'Болт М12','InitialSum':{'Currency':'RUB'},
        'Lots':[{'JointObjects':[{'Name':'Болт М12','Quantity':10,'Okei':'шт','ItemPrice':50,'PositionPrice':500}]}]}).encode()
    ref = s.artifacts.register(s.actor, RegisterArtifact(content, 'purchases.json', 'application/json', {}, 'review-file'))
    body = dict(name='Файл проверки', source='file', source_artifact_id=str(ref.artifact_id),
                settings_snapshot_id=s.snapshot['id'], command_key='review-file-run')
    response = s.client.post(API, json=body)
    assert response.status_code == 202, response.text
    assert s.service.run_once() is True
    saved = s.client.get(API+'/'+response.json()['run_id']).json()
    assert saved['cards'][0]['raw_tender']['id'] == 'file-review'
    assert saved['data']['detail_attempts'] == 1
    assert s.source.remaining == {'search':4, 'purchase':3}
    for value in ['not-uuid', 5, {}, True]:
        assert s.client.post(API, json=dict(body,source_artifact_id=value,command_key='bad-source')).status_code == 422


def test_official_permission_is_explicit_and_default_is_guarded(monkeypatch):
    default = KonturClient('private-fixture-key')
    monkeypatch.setattr(default, '_request', lambda *a, **k: pytest.fail('default reached network'))
    with pytest.raises(CoreError, match='симулятора'):
        default.search_page('болт', 0, '2026-01-01', '2026-11-05')
    permitted = KonturClient('private-fixture-key', allow_configured_api=True)
    calls = []
    def request(path, body=None, **kwargs):
        calls.append((path, body))
        return [{'LimitGroup':'search','AvailableRequests':3}] if kwargs.get('array') else {'Items':[], 'TotalCount':0, 'PageNumber':0}
    monkeypatch.setattr(permitted, '_request', request)
    assert permitted.read_limits()[0]['AvailableRequests'] == 3
    assert permitted.search_page('болт', 0, '2026-01-01', '2026-11-05')['TotalCount'] == 0
    assert [p for p, _ in calls] == ['/external/v1/limitGroups','/external/v1/search']


def test_closed_brief_saves_complete_dto(environment, run_scenario, monkeypatch):
    pipeline, job, run, profile, stock = environment[1:]
    source = pipeline.sources['test_api']
    source.cards = {'closed': tender('closed')}
    monkeypatch.setattr(source, 'search_page', lambda *a: {'Items':[{'Id':'closed','OrderName':'Закрытая закупка','IsCancelled':True}], 'PageNumber':0,'TotalCount':1})
    # Prefilter considers closed state; simulate that branch without changing matching/calculation.
    from bervel.modules.ai_tender_radar.prefilter import CatalogPrefilter
    rank = CatalogPrefilter.rank
    monkeypatch.setattr(CatalogPrefilter, 'rank', lambda self, rows, **kwargs:[dict(row,state='closed') for row in rank(self, rows, **kwargs)])
    s = run_scenario
    receipt = s.client.post(API,json=dict(name='Закрытые',source='test_api',settings_snapshot_id=s.snapshot['id'],command_key='closed-review')).json()
    run_id = UUID(receipt['run_id'])
    pipeline.repository = s.service.run_pipeline.repository
    job['data_scope'] = s.actor.data_scope
    run['run_id'] = run_id
    execute(environment)
    response = s.client.get(API+'/'+str(run_id))
    assert response.status_code == 200
    value = response.json()
    card = value['cards'][0]
    assert datetime.fromisoformat(card['evaluated_at']).utcoffset() is not None
    assert all(key in card and card[key] is None for key in ['tender','analysis','fulfillment','assessment','review'])
    target = Path('.dev/local/radar-unified-2026-11-05/fixes/closed-run-response.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')


def test_explicit_official_source_executes_composed_client_mock_transport_only(run_scenario, monkeypatch):
    s = run_scenario
    official = s.service.run_pipeline.sources['kontur_api']
    calls = []
    def request(path, body=None, **kwargs):
        calls.append(path)
        return ([{'LimitGroup':'search','AvailableRequests':5},{'LimitGroup':'purchase','AvailableRequests':5}]
                if kwargs.get('array') else {'Items':[],'TotalCount':0,'PageNumber':0})
    monkeypatch.setattr(official,'_request',request)
    response = s.client.post(API,json=dict(name='Явный официальный источник',source='kontur_api',
        settings_snapshot_id=s.snapshot['id'],command_key='official-review'))
    assert response.status_code == 202
    assert s.service.run_once() is True
    saved = s.client.get(API+'/'+response.json()['run_id']).json()
    assert saved['data']['state'] == 'complete'
    assert saved['data']['source'] == 'kontur_api'
    assert calls[0] == '/external/v1/limitGroups' and '/external/v1/search' in calls
    assert s.source.remaining == {'search':4,'purchase':3}


def test_explicit_resume_replenishes_provider_quota_without_reset(environment):
    pipeline, job, run = environment[1:4]
    source = pipeline.sources['test_api'] = Source([tender('one'),tender('two')],detail_quota=1)
    execute(environment)
    before = deepcopy(pipeline.repository.state)
    assert before['detail_attempts'] == 1
    run['data'] = before
    job.update(job_id=uuid4(), operation={'kind':'resume'})
    source.detail_quota = 1
    execute(environment)
    state = pipeline.repository.state
    assert state['counts']['accepted'] == 2
    assert state['detail_attempts'] == 2 and state['model_attempts'] == 2
    assert source.fetches == ['one','two'] and source.limit_reads == 2
    assert state['effective_budgets']['detail'] == 2
    assert len(state['quota_epochs']) == 2
    assert state['quota_epochs'][-1]['used']['detail'] == 1


def test_failed_saved_card_manual_retry_atomic_pg_preserves_history(environment, run_scenario, monkeypatch):
    from bervel.modules.ai_tender_radar import named_runs
    pipeline, job, run, profile, stock = environment[1:]
    monkeypatch.setattr('bervel.modules.ai_tender_radar.review.review_card', lambda *a, **kw:
        {'state':'complete','recommendation':'Проверено по сохранённым данным','risks':[], 'assumptions':[], 'evidence_refs':['assessment']})
    s = run_scenario
    repo = pipeline.repository = s.service.run_pipeline.repository
    job['data_scope'] = s.actor.data_scope
    repo.ensure(s.actor.data_scope, run['run_id'],UUID(s.snapshot['id']),run['data'])
    class Saved:
        @property
        def saved(self):
            return {row['candidate_id']:row['data'] for row in repo.cards(run['run_id'])}
    pipeline.model.repository = Saved()
    real_calculation = named_runs.calculate_analysis
    monkeypatch.setattr(named_runs, 'calculate_analysis', lambda *a: (_ for _ in ()).throw(CoreError('ANALYSIS_UNAVAILABLE','technical failure')))
    execute(environment)
    failed = repo.cards(run['run_id'])[0]['data']
    assert failed['state'] == 'failed'
    saved_state = repo.read(job['data_scope'],run['run_id'])['data']
    # Use matching scope for the next run state writes.
    job['data_scope'] = s.actor.data_scope
    run['data'] = dict(saved_state,detail_attempts=1,model_attempts=1,limits=[],effective_budgets={'search':1,'detail':1},
        checkpoint={'candidates':[{'id':'one','brief':{'Id':'one','OrderName':'Болт М8'},'state':'candidate'}],
                    'branches':[],'search_attempts':1,'stop_reason':'search_exhausted'})
    job.update(job_id=uuid4(),operation={'kind':'resume'})
    monkeypatch.setattr(named_runs,'calculate_analysis',real_calculation)
    # Actual matcher needs the test repository only to assert saved immutable source.
    pipeline.model.repository = type('Saved',(),{'saved':{'one':failed}})()
    execute(environment)
    result = repo.cards(run['run_id'])[0]['data']
    assert result['state'] == 'accepted'
    assert result['raw_tender'] == failed['raw_tender'] and result['evaluated_at'] == failed['evaluated_at']
    assert result['attempts'][0]['result'] == failed
    assert 'error' not in result
    assert pipeline.sources['test_api'].fetches == ['one']
    assert repo.retry_failed_card(run['run_id'],'one',str(uuid4())) is None
    state = repo.read(s.actor.data_scope,run['run_id'])['data']
    assert state['detail_attempts'] == 1 and state['model_attempts'] == 2
    response = s.client.get(API+'/'+str(run['run_id']))
    assert response.status_code == 200
    target = Path('.dev/local/radar-unified-2026-11-05/fixes/rich-run-response.json')
    target.write_text(json.dumps(response.json(),ensure_ascii=False),encoding='utf-8')


def test_provable_raw_reject_with_documents_skips_enrichment_and_model(environment, monkeypatch):
    pipeline = environment[1]
    value = tender('one', quantity='100')
    value['documents'] = [{'text':'Untrusted source text'}]
    pipeline.sources['test_api'].cards['one'] = value
    monkeypatch.setattr('bervel.modules.ai_tender_radar.documents.enrich_tender',
        lambda *a, **k: pytest.fail('proven raw reject must not invoke document model'))
    execute(environment)
    card = pipeline.repository.saved['one']
    assert card['state'] == 'irrelevant' and card['analysis'] is None
    assert card['selection']['reason'] == 'STOCK_UPPER_BOUND_BELOW_80'
    assert pipeline.model.calls == 0


def test_manual_resume_quota_reserves_unknown_outcomes_and_replays_no_refresh(environment):
    pipeline, job, run = environment[1:4]
    source = pipeline.sources['test_api']
    source.search_quota = source.detail_quota = 3
    run['data'].update(limits=[], effective_budgets={'search':4,'detail':2},
        detail_attempts=2, model_attempts=7, pending_detail='lost-request',
        checkpoint={'candidates':[{'id':'one','brief':{'Id':'one','OrderName':'Болт М8'},'state':'candidate'}],
            'branches':[],'search_attempts':4,'pending_search':{'phrase':'болт','page':2},'stop_reason':'search_exhausted'})
    job.update(job_id=uuid4(),operation={'kind':'resume'})
    execute(environment)
    state = pipeline.repository.state
    epoch = state['quota_epochs'][-1]
    assert epoch['used'] == {'search':4,'detail':2}
    assert epoch['unknown'] == {'search':1,'detail':1}
    assert epoch['allowed_total'] == {'search':6,'detail':4}
    assert state['detail_attempts'] == 3 and state['model_attempts'] == 8
    run['data'] = deepcopy(state)
    execute(environment)
    assert source.limit_reads == 1 and source.fetches == ['one']
    assert pipeline.repository.state['model_attempts'] == 8


def test_http_pg_manual_resume_uses_replenished_credits_and_keeps_accepted(run_scenario, monkeypatch):
    s = run_scenario
    source = Source([tender('one',name='Болт М12'),tender('two',name='Болт М12')],detail_quota=1)
    s.service.run_pipeline.sources['test_api'] = source
    response = s.client.post(API,json=dict(name='Пополнение квоты',source='test_api',settings_snapshot_id=s.snapshot['id'],command_key='quota-http'))
    assert response.status_code == 202
    assert s.service.run_once() is True
    url = API+'/'+response.json()['run_id']
    before = s.client.get(url).json()
    assert before['data']['stop_reason'] == 'detail_quota'
    first = before['cards'][0]
    assert first['state'] == 'accepted'
    source.detail_quota = 1
    queued = s.client.post(url+'/resume',json={'command_key':'quota-http-resume'})
    assert queued.status_code == 202,queued.text
    assert s.service.run_once() is True
    after = s.client.get(url).json()
    assert after['data']['state'] == 'complete' and after['data']['counts']['accepted'] == 2
    assert after['cards'][0] == first
    assert after['data']['detail_attempts'] == 2 and source.fetches == ['one','two']
    assert len(after['data']['quota_epochs']) == 2


def test_http_pg_manual_resume_recovers_saved_technical_failure_without_refetch(run_scenario, monkeypatch):
    from bervel.modules.ai_tender_radar import named_runs
    s = run_scenario
    source = Source([tender('one',name='Болт М12')],detail_quota=1)
    s.service.run_pipeline.sources['test_api'] = source
    calculate = named_runs.calculate_analysis
    monkeypatch.setattr(named_runs,'calculate_analysis',lambda *a: (_ for _ in ()).throw(CoreError('ANALYSIS_UNAVAILABLE','technical error')))
    response = s.client.post(API,json=dict(name='Повтор ошибки',source='test_api',settings_snapshot_id=s.snapshot['id'],command_key='failure-http'))
    assert response.status_code == 202
    assert s.service.run_once() is True
    url = API+'/'+response.json()['run_id']
    before = s.client.get(url).json()
    failed = before['cards'][0]
    assert failed['state'] == 'failed'
    monkeypatch.setattr(named_runs,'calculate_analysis',calculate)
    response = s.client.post(url+'/resume',json={'command_key':'failure-http-resume'})
    assert response.status_code == 202,response.text
    assert s.service.run_once() is True
    after = s.client.get(url).json()
    card = after['cards'][0]
    assert card['state'] == 'accepted' and source.fetches == ['one']
    assert card['raw_tender'] == failed['raw_tender'] and card['tender'] == failed['tender']
    assert card['attempts'][0]['result'] == {key: value for key, value in failed.items() if key not in {'metrics', 'opportunity'}}
    assert after['data']['detail_attempts'] == before['data']['detail_attempts'] == 1
    assert after['data']['model_attempts'] > before['data']['model_attempts']
