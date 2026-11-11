"""Profile-run HTTP/worker contracts using PostgreSQL and a loopback-only source."""
from copy import deepcopy
from datetime import datetime,timezone
import json
from threading import Thread
from types import SimpleNamespace
from uuid import UUID,uuid4

from fastapi.testclient import TestClient
import pytest

from kontur_simulator import create_server
from bervel.ai_tender_radar_setup import build_ai_tender_radar
from bervel.core.api.app import create_platform_app
from bervel.core.config import Settings
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext,RegisterArtifact
from bervel.modules.ai_tender_radar.api import ai_tender_radar_router,runs_router
from bervel.modules.company_settings.api import company_settings_router
from test_ai_tender_radar_analysis import SelectingModel

API = '/api/v0.1/ai-tender-radar/runs'
SETTINGS = '/api/v0.1/settings/company'


class WorkflowModel:
    """Only substitutes the external model, never the source/queue/calculators."""
    def __init__(self):
        self.calls = []
        self.matcher = SelectingModel()

    def generate(self,request_id,prompt,*,output_schema,timeout_seconds=None,web_search=False):
        assert web_search is False
        payload = json.loads(prompt.split('\nВХОД\n',1)[1])
        if output_schema is None:
            if request_id.startswith('card-rating-'):
                self.calls.append({'request_id': request_id, 'kind': ('individual_rating',), 'payload': deepcopy(payload)})
                return {'model': 'workflow-fixture', 'answer': '83'}
            assert len(payload['candidates']) == 1
            self.calls.append({'request_id':request_id,'kind':('priority',),'payload':deepcopy(payload)})
            return {'model':'workflow-fixture','answer':'60'}
        properties = output_schema['properties']
        self.calls.append({'request_id':request_id,'kind':tuple(properties),'payload':deepcopy(payload)})
        if 'positions' in properties:
            return self.matcher.generate(request_id,prompt,output_schema=output_schema,timeout_seconds=timeout_seconds)
        if 'groups' in properties:
            return {'model':'workflow-fixture','answer':json.dumps({'groups':[
                dict(id=group['id'], keywords=[group['name'].split()[0]]) for group in payload['groups']
            ]},ensure_ascii=False)}
        if 'candidates' in properties:
            if 'catalog_groups' in payload:
                return {'model':'workflow-fixture','answer':json.dumps({'candidates':[
                    dict(id=c['id'],relevance='possible',score=60,
                         product_ids=[p['id'] for p in c['products'][:1]],
                         quote=c['title'],reason='Есть указание на вид товара.')
                    for c in payload['candidates']]},ensure_ascii=False)}
            products = [p['id'] for p in payload['products']]
            return {'model':'workflow-fixture','answer':json.dumps({'candidates':[
                dict(id=c['id'],relevance='possible',product_ids=products[:1],quote=c['title'])
                for c in payload['candidates']]},ensure_ascii=False)}
        if 'terms' in properties and 'inventory' in properties:
            return {'model':'workflow-fixture','answer':json.dumps({'terms':{key:None for key in properties['terms']['properties']},
                'requirements':[], 'inventory':{'complete':None,'rows':[]}},ensure_ascii=False)}
        research = 'action' in properties
        assert research or 'calculated' in properties
        answer = dict(recommendation='Требуется проверить подтверждённые условия.',risks=[],assumptions=[],
                      evidence_refs=['assessment'],calculated=payload['calculated'],decision='review',proposes_production=False)
        if research:
            answer.update(recommendation='Требуется проверить подтверждённые условия.\n\nСроки и затраты нужно уточнить.',
                          evidence_refs=['card:/assessment'])
            if 'question' in payload:
                answer.update(answer='Ответ относится к сохранённому расчёту.', calculations=[])
            answer = dict(action='final', ref='', offset=0, length=16000, query='', notes='', response=answer)
        elif 'answer' in properties:
            answer['answer'] = 'Ответ относится к сохранённому расчёту.'
        return {'model':'workflow-fixture','answer':json.dumps(answer,ensure_ascii=False)}


@pytest.fixture
def run_scenario(services,pg_engine,tmp_path):
    artifacts,_,_,original = services
    permissions = original.permissions | {'settings:read','settings:write','process:read','process:start','process:recover'}
    actor = ActorContext(original.actor_id,original.data_scope,permissions)
    keys = {
        'a'*32:actor,
        'b'*32:ActorContext('outsider','other-scope',permissions),
        'c'*32:ActorContext('viewer',actor.data_scope,permissions-{'process:start'}),
        'd'*32:ActorContext('no-artifact',actor.data_scope,permissions-{'artifact:read'}),
    }
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with create_server(port=0,profile='selection',seed=7319,today=stamp,max_requests_per_second=0) as source_server:
        source_server.remaining.update(search=4,purchase=3)
        thread = Thread(target=source_server.serve_forever)
        thread.start()
        try:
            source_url = f'http://127.0.0.1:{source_server.server_port}'
            model = WorkflowModel()
            service = build_ai_tender_radar(pg_engine,artifacts,'unused-private-key',model,kontur_base_url=source_url)
            service.kontur.min_interval = 0
            assert service.kontur.base_url.startswith('http://127.0.0.1:')
            settings = Settings('unused',tmp_path/'objects',keys,ai_radar_kontur_base_url=source_url)
            app,dependency = create_platform_app(settings,pg_engine,artifacts)
            app.include_router(ai_tender_radar_router(service,dependency))
            app.include_router(runs_router(service.run_pipeline,dependency))
            company = service.run_pipeline.settings
            app.include_router(company_settings_router(company,dependency))
            lines = ['sku,product,free_quantity,unit,unit_cost,currency,as_of,standard,length_mm,diameter_mm,family',
                     f'B12,Болт М12,100000,шт,25,RUB,{stamp},ГОСТ 7798-70,50,12,закупка',
                     f'B16,Болт М16,100000,шт,25,RUB,{stamp},ГОСТ 7805-70,80,16,закупка',
                     f'N12,Гайка М12,100000,шт,25,RUB,{stamp},ГОСТ 5915-70,,12,закупка',
                     f'W16,Шайба М16,100000,шт,25,RUB,{stamp},ГОСТ 11371-78,,16,закупка']
            ref = artifacts.register(actor,RegisterArtifact(('\n'.join(lines)+'\n').encode(),'stock.csv','text/csv',{},'run-stock'))
            artifacts.set_usage_role(actor,ref.artifact_id,'stock')
            with TestClient(app) as client:
                client.headers['Authorization'] = 'Bearer '+'a'*32
                preview = client.post(SETTINGS+'/preview',json={'stock_artifact_id':str(ref.artifact_id)})
                assert preview.status_code == 200,preview.text
                data = preview.json()['profile']
                data['scoring_policy'] = {'max_age_by_source':{'stock':'7','costs':'7','resources':'7','production':'7'}}
                saved = client.put(SETTINGS,json={'base_snapshot_id':None,'profile':data,'command_key':'run-profile'})
                assert saved.status_code == 200,saved.text
                snapshot = saved.json()['snapshot']
                yield SimpleNamespace(client=client,actor=actor,artifacts=artifacts,company=company,service=service,
                    source=source_server,model=model,snapshot=snapshot,engine=pg_engine)
        finally:
            source_server.shutdown()
            thread.join()


def command(scenario,key='run-first',**changes):
    return dict(settings_snapshot_id=scenario.snapshot['id'],command_key=key,search_max=4,detail_max=3,model_max=30,**changes)


def enqueue(scenario,body=None):
    response = scenario.client.post(API,json=body or command(scenario))
    assert response.status_code == 202,response.text
    return response.json()


def completed(scenario,body=None):
    receipt = enqueue(scenario,body)
    assert scenario.service.run_once() is True
    response = scenario.client.get(API+'/'+receipt['run_id'])
    assert response.status_code == 200,response.text
    return receipt,response.json()


def test_audit_progress_can_finalize_but_final_report_is_immutable(run_scenario):
    s=run_scenario
    receipt,_=completed(s)
    repository=s.service.run_pipeline.repository
    run_id=UUID(receipt['run_id'])
    job_id=uuid4()
    pending={'kind':'audit','result':{'status':'running','checked':0,'total':3}}
    final={'kind':'audit','result':{'status':'partial','checked':2,'total':3}}
    repository.report(job_id,run_id,pending)
    assert repository.read_reports(run_id)[0]['data']==pending
    repository.report(job_id,run_id,final)
    repository.report(job_id,run_id,{'kind':'audit','result':{'status':'complete','checked':3,'total':3}})
    assert repository.read_reports(run_id)[0]['data']==final


def test_missing_requirements_are_not_classified_as_irrelevant(run_scenario,monkeypatch):
    s=run_scenario
    fetch=s.service.kontur.fetch_tender
    def incomplete(*args,**kwargs):
        tender=fetch(*args,**kwargs)
        tender['positions']=[]
        tender['limitations']=[{'code':'REQUIREMENTS_MISSING'}]
        tender['documents']=[]
        return tender
    monkeypatch.setattr(s.service.kontur,'fetch_tender',incomplete)
    _,run=completed(s)
    assert all(card['state']=='needs_data' for card in run['cards'])


def test_http_run_uses_real_source_queue_full_snapshots_and_explicit_budgets(run_scenario):
    s = run_scenario
    original = deepcopy(s.snapshot)
    receipt,run = completed(s)
    assert run['settings_snapshot_id'] == original['id']
    assert run['data']['environment'] == 'simulation'
    assert run['data']['state'] == 'complete'
    assert run['data']['checkpoint']['search_attempts'] == 4
    assert run['data']['detail_attempts'] == 3
    assert s.source.remaining == {'search':0,'purchase':0}
    assert len(run['cards']) == 3
    assert len({c['candidate_id'] for c in run['cards']}) == 3
    assert run['data']['counts']['looked'] == 160
    assert sum(run['data']['counts'][kind] for kind in ('accepted','needs_data','irrelevant','failed')) == 3
    for card in run['cards']:
        assert card['tender']['source_card']['Id'] == card['candidate_id']
        assert card['tender']['simulated'] is True
        assert isinstance(card['tender'].get('documents',[]),list)
        assert {'requirements','economics','schedule','complete','reasons'} <= card['completeness'].keys()
        assert card['analysis'] is not None
        assert card['fulfillment']['status'] in {'feasible','infeasible','needs_review'}
        assert card['assessment']['score'] is None or 0<=card['assessment']['score']<=100
        assert card['review']['state'] == 'complete'
        assert card['review']['model_version'] == 'workflow-fixture'
    assert s.client.get(SETTINGS).json()['snapshot'] == original
    assert s.client.get(API).json()['runs'][0]['run_id'] == receipt['run_id']
    assert s.service.run_once() is False


def test_run_command_replays_once_and_changed_budgets_conflict(run_scenario):
    s = run_scenario
    body = command(s)
    first = enqueue(s,body)
    replay = enqueue(s,body)
    assert replay == first
    changed = dict(body,detail_max=2)
    rejected = s.client.post(API,json=changed)
    assert rejected.status_code == 409,rejected.text
    assert rejected.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    assert s.source.remaining == {'search':4,'purchase':3}
    assert s.service.run_once()
    assert s.service.run_once() is False
    after = enqueue(s,body)
    assert after['run_id'] == first['run_id']
    assert len(s.client.get(API).json()['runs']) == 1


def test_queued_run_keeps_old_profile_after_new_settings_are_saved(run_scenario):
    s = run_scenario
    old = deepcopy(s.snapshot)
    receipt = enqueue(s)
    changed = deepcopy(old['profile'])
    for product in changed['products']:
        product['enabled'] = False
    changed['products'][0]['name'] = 'Ручная новая версия'
    saved = s.client.put(SETTINGS,json={'base_snapshot_id':old['id'],'profile':changed,'command_key':'new-profile'})
    assert saved.status_code == 200,saved.text
    assert saved.json()['snapshot']['id'] != old['id']
    assert s.service.run_once()
    run = s.client.get(API+'/'+receipt['run_id']).json()
    assert run['settings_snapshot_id'] == old['id']
    assert len(run['cards']) == 3
    assert any(line['matches'] for card in run['cards'] for line in card['analysis']['positions'])
    assert s.company.read_snapshot(s.actor.data_scope,UUID(old['id']))['profile'] == old['profile']
    assert s.client.get(SETTINGS).json() == saved.json()
    archived = deepcopy(run['cards'])
    assert s.client.get(API+'/'+receipt['run_id']).json()['cards'] == archived


def test_other_scopes_and_read_only_users_cannot_enqueue_or_read_the_run(run_scenario):
    s = run_scenario
    receipt = enqueue(s)
    s.client.headers['Authorization'] = 'Bearer '+'b'*32
    assert s.client.get(API+'/'+receipt['run_id']).status_code in {403,404}
    assert s.client.get(API).json()['runs'] == []
    assert s.client.post(API,json=command(s,'foreign')).status_code in {403,404}
    assert s.client.post(API+'/'+receipt['run_id']+'/audit',json={'command_key':'foreign-audit'}).status_code in {403,404}
    s.client.headers['Authorization'] = 'Bearer '+'c'*32
    assert s.client.get(API+'/'+receipt['run_id']).status_code == 200
    assert s.client.post(API,json=command(s,'viewer')).status_code == 403
    assert s.client.post(API+'/'+receipt['run_id']+'/audit',json={'command_key':'viewer-audit'}).status_code == 403
    s.client.headers['Authorization'] = 'Bearer '+'d'*32
    assert s.client.get(API+'/'+receipt['run_id']).status_code == 403
    assert s.client.post(API,json=command(s,'no-artifact')).status_code == 403


def test_questions_and_audits_are_queued_separate_reports_with_manual_reference(run_scenario):
    s = run_scenario
    receipt,run = completed(s)
    cards = deepcopy(run['cards'])
    first = cards[0]
    path = API+'/'+receipt['run_id']
    question = {'command_key':'question-old','analysis_id':first['analysis_id'],'question':'Почему такой результат?'}
    asked = s.client.post(path+'/question',json=question)
    assert asked.status_code == 202,asked.text
    assert s.client.post(path+'/question',json=question).json()['job_id'] == asked.json()['job_id']
    assert s.service.run_once()
    answered = s.client.get(path).json()
    [report] = [entry for entry in answered['reports']
                if entry['job_id'] == asked.json()['job_id'] and entry['kind'] == 'question']
    assert report['analysis_id'] == first['analysis_id']
    assert report['result']['state'] == 'complete'
    assert report['result']['answer'] == 'Ответ относится к сохранённому расчёту.'
    reference = {first['analysis_id']:{'score':first['assessment']['score'],'feasibility':first['fulfillment']['status']}}
    audit = s.client.post(path+'/audit',json={'command_key':'audit-old','reference':reference,'max_cards':3})
    assert audit.status_code == 202,audit.text
    assert s.service.run_once()
    final = s.client.get(path).json()
    [audit_report] = [entry for entry in final['reports']
                      if entry['job_id'] == audit.json()['job_id'] and entry['kind'] == 'audit']
    audited = audit_report['result']
    assert audited['checked'] == 3,audited['findings']
    assert audited['total'] == 160
    assert audited['status'] == 'partial'
    assert len(audited['unchecked_analysis_ids']) == 157
    assert set(audited['unchecked_analysis_ids']) == {'brief:'+c['id'] for c in run['candidates']}
    assert audited['reference_comparison']['provided'] is True
    assert audited['reference_comparison']['checked'] == 1
    assert final['cards'] == cards
    assert s.source.remaining == {'search':0,'purchase':0}


@pytest.mark.parametrize('failure',['unknown','429'])
def test_explicit_resume_keeps_failed_search_attempts_in_the_original_budget(run_scenario,monkeypatch,failure):
    s = run_scenario
    source = s.service.kontur
    request = source._request
    calls = []
    failed = False
    def faulty(path,body=None,*,array=False):
        nonlocal failed
        if path.endswith('/search'):
            calls.append(deepcopy(body))
            if not failed:
                failed = True
                if failure=='429':
                    s.source.remaining['search'] = 0
                    return request(path,body,array=array)
                request(path,body,array=array)
                raise CoreError('KONTUR_UNAVAILABLE','Ответ поисковой попытки потерян после отправки.')
        return request(path,body,array=array)
    monkeypatch.setattr(source,'_request',faulty)
    receipt = enqueue(s)
    assert s.service.run_once()
    failed_run = s.client.get(API+'/'+receipt['run_id']).json()
    assert failed_run['data']['state'] == 'failed'
    assert failed_run['data']['checkpoint']['search_attempts'] == 1
    assert failed_run['data']['checkpoint']['pending_search']
    assert len(calls)==1
    assert s.service.run_once() is False
    if failure=='429':
        s.source.remaining['search'] = 4
    path = API+'/'+receipt['run_id']+'/resume'
    resumed = s.client.post(path,json={'command_key':'resume-original'})
    assert resumed.status_code == 202,resumed.text
    assert s.service.run_once()
    final = s.client.get(API+'/'+receipt['run_id']).json()
    assert final['data']['checkpoint']['search_attempts'] == 4
    assert final['data']['detail_attempts'] == 3
    assert final['data']['budgets'] == {'search':4,'detail':3,'model':30}
    assert len(calls)==4
    assert final['data']['state'] == 'complete'
    assert len(final['cards']) == 3
    assert len(s.client.get(API).json()['runs']) == 1
    replay = s.client.post(path,json={'command_key':'resume-original'})
    assert replay.status_code == 202,replay.text
    assert replay.json()['job_id'] == resumed.json()['job_id']


def test_exhausted_model_budget_does_not_call_model_or_label_technical_failures_irrelevant(run_scenario):
    s = run_scenario
    body = dict(command(s),model_max=0)
    receipt,run = completed(s,body)
    assert s.model.calls == []
    assert run['data'].get('model_attempts',0) == 0
    assert len(run['cards']) == 3
    for card in run['cards']:
        assert card['review']['state'] == 'failed'
        if card['analysis'].get('model_error'):
            assert card['state'] != 'irrelevant'
            assert card['analysis']['model_error']['code'] == 'MODEL_QUOTA_EXCEEDED'


@pytest.mark.parametrize('changes',[{'search_max':201},{'detail_max':0},{'model_max':1001},{'search_max':True},{'settings_snapshot_id':'invalid'},{'extra':True}])
def test_invalid_run_contract_is_rejected_before_any_source_access(run_scenario,changes):
    s = run_scenario
    response = s.client.post(API,json={**command(s),**changes})
    assert response.status_code == 422,response.text
    assert s.source.remaining == {'search':4,'purchase':3}
    assert s.client.get(API).json()['runs'] == []


def test_job_and_run_become_visible_to_worker_in_one_atomic_publication(run_scenario):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import event
    s = run_scenario
    paused,release = Event(),Event()
    def before_run_insert(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().lower().startswith('insert into ai_radar_runs'):
            paused.set()
            if not release.wait(10):
                raise AssertionError('Timed out waiting for the publication observer')
    event.listen(s.engine,'before_cursor_execute',before_run_insert)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(s.client.post,API,json=command(s))
            try:
                assert paused.wait(10),'Enqueue did not reach the run publication boundary'
                # A separate database connection represents the worker racing the API transaction.
                assert s.service.jobs.claim() is None
                assert s.service.run_pipeline.repository.list(s.actor.data_scope)['runs'] == []
            finally:
                release.set()
            response = pending.result(timeout=10)
    finally:
        release.set()
        event.remove(s.engine,'before_cursor_execute',before_run_insert)
    assert response.status_code == 202,response.text
    assert s.service.run_once()
    run = s.client.get(API+'/'+response.json()['run_id']).json()
    assert run['data']['state'] == 'complete'
    assert len(run['cards']) == 3


def test_failed_run_publication_rolls_back_the_queued_job(run_scenario):
    from sqlalchemy import event
    s = run_scenario
    def fail_run_insert(conn,cursor,statement,parameters,context,executemany):
        if statement.lstrip().lower().startswith('insert into ai_radar_runs'):
            raise RuntimeError('Injected publication failure')
    event.listen(s.engine,'before_cursor_execute',fail_run_insert)
    try:
        with pytest.raises(RuntimeError,match='Injected publication failure'):
            s.client.post(API,json=command(s))
    finally:
        event.remove(s.engine,'before_cursor_execute',fail_run_insert)
    assert s.service.jobs.claim() is None
    assert s.client.get(API).json()['runs'] == []
    assert s.source.remaining == {'search':4,'purchase':3}


@pytest.mark.parametrize('production_version',[1,2])
def test_crash_after_detail_keeps_raw_snapshot_and_resume_never_fetches_it_again(run_scenario,monkeypatch,production_version):
    from dataclasses import replace
    from sqlalchemy import text
    from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
    s = run_scenario
    generate = s.model.generate
    request = s.service.kontur._request
    fetches = []
    def traced_request(path,body=None,*,array=False):
        if '/purchases/' in path:
            fetches.append(path.rsplit('/',1)[-1])
        return request(path,body,array=array)
    def crash_after_detail(request_id,prompt,**options):
        if 'candidates' not in (options.get('output_schema') or {}).get('properties', {}):
            raise RuntimeError('Injected process crash before detail model response')
        return generate(request_id,prompt,**options)
    monkeypatch.setattr(s.service.kontur,'_request',traced_request)
    monkeypatch.setattr(s.model,'generate',crash_after_detail)
    receipt = enqueue(s,dict(command(s),search_max=1,detail_max=1))
    with pytest.raises(RuntimeError,match='Injected process crash'):
        s.service.run_once()
    interrupted = s.client.get(API+'/'+receipt['run_id']).json()
    assert interrupted['data']['detail_attempts'] == 1
    assert len(interrupted['cards']) == 1
    raw = deepcopy(interrupted['cards'][0])
    assert raw['state'] == 'analyzing'
    assert raw['tender']['source_card']['Id'] == raw['candidate_id']
    assert raw['analysis'] is None
    assert fetches == [raw['candidate_id']]
    # A historical interrupted run retains its production policy when resumed.
    repository=s.service.run_pipeline.repository
    state=deepcopy(interrupted['data'])
    state['stage_versions']['production']=production_version
    repository.update(s.actor.data_scope,UUID(receipt['run_id']),state)
    # The source changes after the crash; resumption must use the already accepted snapshot.
    purchase = s.source.purchases[raw['candidate_id']]
    s.source.purchases[raw['candidate_id']] = replace(purchase,name='Changed after crash',fixed_quantity=9999)
    assert s.source.purchases[raw['candidate_id']].card() != raw['tender']['source_card']
    with s.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id==UUID(receipt['job_id'])).values(
            lease_until=text("now() - interval '1 second'")))
    assert s.service.run_once() is False
    failed = s.client.get(API+'/'+receipt['run_id']).json()
    assert failed['data']['state'] == 'failed'
    monkeypatch.setattr(s.model,'generate',generate)
    resumed = s.client.post(API+'/'+receipt['run_id']+'/resume',json={'command_key':'resume-raw'})
    assert resumed.status_code == 202,resumed.text
    assert s.service.run_once()
    final = s.client.get(API+'/'+receipt['run_id']).json()
    assert final['data']['state'] == 'complete'
    assert final['data']['detail_attempts'] == 1
    assert len(final['cards']) == 1
    result = final['cards'][0]
    assert result['analysis_id'] == raw['analysis_id']
    assert result['evaluated_at'] == raw['evaluated_at']
    assert result['raw_tender'] == raw['tender']
    enriched = deepcopy(result['tender'])
    extraction = enriched.pop('document_extraction')
    assert extraction['state'] == 'complete',(extraction,[d['text'] for d in raw['tender'].get('documents',[])])
    assert enriched == raw['tender']
    assert result['state'] != 'analyzing'
    assert result['analysis'] is not None
    assert result['fulfillment']['version']==production_version
    assert final['data']['stage_versions']['production']==production_version
    assert fetches == [raw['candidate_id']]
    assert s.source.remaining['purchase'] == 2


@pytest.mark.parametrize('answer',['[]','[null]','{"candidates":[null]}'])
def test_malformed_reranking_json_falls_back_without_crashing_worker(run_scenario,monkeypatch,answer):
    s = run_scenario
    generate = s.model.generate
    def malformed(request_id,prompt,**options):
        if 'candidates' in (options.get('output_schema') or {}).get('properties', {}):
            return {'answer':answer,'model':'malformed-fixture'}
        return generate(request_id,prompt,**options)
    monkeypatch.setattr(s.model,'generate',malformed)
    receipt,run = completed(s)
    assert run['data']['state'] == 'complete'
    assert any(w['reason']=='MODEL_RANKING_UNAVAILABLE' for w in run['data']['ranking_warnings'])
    assert len(run['cards']) == 3
    assert all(c['tender'] and c['analysis'] for c in run['cards'])
    assert all(c['review']['state']=='complete' for c in run['cards'])
    assert s.source.remaining == {'search':0,'purchase':0}


def test_null_items_over_real_http_becomes_typed_failure_with_persisted_attempt(run_scenario,monkeypatch):
    s = run_scenario
    search = s.source.search
    def null_items(query):
        result = search(query)
        result['Items'] = None
        return result
    # The real simulator HTTP handler serializes this as JSON null on the wire.
    monkeypatch.setattr(s.source,'search',null_items)
    receipt = enqueue(s)
    assert s.service.run_once()
    run = s.client.get(API+'/'+receipt['run_id']).json()
    assert run['data']['state'] == 'failed'
    assert run['data']['error']['code'] == 'KONTUR_INVALID_RESPONSE'
    assert run['data']['checkpoint']['search_attempts'] == 1
    assert run['data']['checkpoint']['pending_search']
    assert run['data'].get('detail_attempts',0) == 0
    assert run['cards'] == []
    assert s.source.remaining == {'search':3,'purchase':3}
    assert s.model.calls == []



def _deny_source_access(s,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('Saved-card reanalysis must not call the source API')
    monkeypatch.setattr(s.service.kontur,'_request',forbidden)


def _reanalyze(s,run_id,analysis_id,key,settings_snapshot_id=None):
    body = {'command_key':key,'analysis_id':analysis_id}
    if settings_snapshot_id is not None:
        body['settings_snapshot_id']=settings_snapshot_id
    response = s.client.post(API+'/'+run_id+'/reanalyze',json=body)
    assert response.status_code==202,response.text
    return response,body


def test_saved_card_reanalysis_uses_historical_or_explicit_current_profile_without_source_calls(run_scenario,monkeypatch):
    s = run_scenario
    original_receipt,original = completed(s)
    source_card = next(c for c in original['cards'] if any(line['matches'] for line in c['analysis']['positions']))
    source_card = deepcopy(source_card)
    changed = deepcopy(s.snapshot['profile'])
    for product in changed['products']:
        product['enabled']=False
    current = s.client.put(SETTINGS,json={'base_snapshot_id':s.snapshot['id'],'profile':changed,'command_key':'disable-current-catalog'})
    assert current.status_code==200,current.text
    current_id = current.json()['snapshot']['id']
    assert current_id!=s.snapshot['id']
    _deny_source_access(s,monkeypatch)
    response,body = _reanalyze(s,original_receipt['run_id'],source_card['analysis_id'],'child-historical')
    old_child_receipt = response.json()
    assert old_child_receipt['run_id']!=original_receipt['run_id']
    replay = s.client.post(API+'/'+original_receipt['run_id']+'/reanalyze',json=body)
    assert replay.status_code==202,replay.text
    assert replay.json()['run_id']==old_child_receipt['run_id']
    assert replay.json()['job_id']==old_child_receipt['job_id']
    assert s.service.run_once()
    historical = s.client.get(API+'/'+old_child_receipt['run_id']).json()
    assert historical['settings_snapshot_id']==s.snapshot['id']
    assert historical['data']['state']=='complete'
    assert historical['data']['source_kind']=='saved_card'
    assert historical['data']['stop_reason']=='saved_card'
    assert historical['data']['checkpoint']['search_attempts']==0
    assert historical['data'].get('detail_attempts',0)==0
    assert historical['data']['budgets']['search']==historical['data']['budgets']['detail']==0
    assert historical['search_pages']==[]
    assert len(historical['cards'])==1
    child_card = historical['cards'][0]
    assert child_card['analysis_id']!=source_card['analysis_id']
    assert child_card['source_analysis_id']==source_card['analysis_id']
    assert child_card['raw_tender']==source_card['raw_tender']
    assert child_card['candidate_id']==source_card['candidate_id']
    assert any(line['matches'] for line in child_card['analysis']['positions'])
    replay_after = s.client.post(API+'/'+original_receipt['run_id']+'/reanalyze',json=body)
    assert replay_after.status_code==202,replay_after.text
    assert replay_after.json()['run_id']==old_child_receipt['run_id']
    conflict = s.client.post(API+'/'+original_receipt['run_id']+'/reanalyze',json={**body,'settings_snapshot_id':current_id})
    assert conflict.status_code==409,conflict.text
    assert conflict.json()['error']['code']=='IDEMPOTENCY_CONFLICT'
    new_response,_ = _reanalyze(s,original_receipt['run_id'],source_card['analysis_id'],'child-current',current_id)
    assert s.service.run_once()
    current_child = s.client.get(API+'/'+new_response.json()['run_id']).json()
    assert current_child['settings_snapshot_id']==current_id
    assert current_child['data']['stop_reason']=='saved_card'
    assert current_child['data'].get('detail_attempts',0)==0
    assert len(current_child['cards'])==1
    current_card = current_child['cards'][0]
    assert current_card['analysis_id'] not in {source_card['analysis_id'],child_card['analysis_id']}
    assert current_card['raw_tender']==source_card['raw_tender']
    assert all(line['matches']==[] for line in current_card['analysis']['positions'])
    assert s.client.get(API+'/'+original_receipt['run_id']).json()==original
    assert len(s.client.get(API).json()['runs'])==3
    assert s.source.remaining=={'search':0,'purchase':0}


def test_failed_child_reanalysis_keeps_parent_history_and_returns_same_child_for_retry(run_scenario,monkeypatch):
    s = run_scenario
    receipt,original = completed(s)
    parent_rows = deepcopy(s.service.run_pipeline.repository.cards(UUID(receipt['run_id'])))
    _deny_source_access(s,monkeypatch)
    response,body = _reanalyze(s,receipt['run_id'],original['cards'][0]['analysis_id'],'child-failure')
    def unavailable_original(context,ref):
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR','Injected inaccessible stock original')
    monkeypatch.setattr(s.artifacts,'read',unavailable_original)
    assert s.service.run_once()
    child = s.client.get(API+'/'+response.json()['run_id']).json()
    assert child['data']['state']=='failed'
    assert child['data']['error']['code']=='TRANSIENT_DEPENDENCY_ERROR'
    assert child['data']['source_run_id']==receipt['run_id']
    parent = s.client.get(API+'/'+receipt['run_id'])
    assert parent.status_code == 200
    assert parent.json()['data'] == original['data']
    assert s.service.run_pipeline.repository.cards(UUID(receipt['run_id'])) == parent_rows
    for card in parent.json()['cards']:
        assert card['opportunity']['score'] == 0
        assert card['opportunity']['basis'] == 'unknown'
        assert 'ORIGINAL_STOCK_UNAVAILABLE' in card['projection']['reasons']
    replay = s.client.post(API+'/'+receipt['run_id']+'/reanalyze',json=body)
    assert replay.status_code==202,replay.text
    assert replay.json()['run_id']==response.json()['run_id']
    assert replay.json()['job_id']==response.json()['job_id']
    assert replay.json()['state']=='failed'
    assert len(s.client.get(API).json()['runs'])==2


def test_reanalysis_checks_parent_scope_card_membership_permissions_and_target_profile_scope(run_scenario,monkeypatch):
    s = run_scenario
    receipt,original = completed(s)
    path = API+'/'+receipt['run_id']+'/reanalyze'
    body = {'command_key':'scope-child','analysis_id':original['cards'][0]['analysis_id']}
    _deny_source_access(s,monkeypatch)
    s.client.headers['Authorization']='Bearer '+'b'*32
    assert s.client.post(path,json=body).status_code in {403,404}
    s.client.headers['Authorization']='Bearer '+'c'*32
    assert s.client.post(path,json=body).status_code==403
    s.client.headers['Authorization']='Bearer '+'d'*32
    assert s.client.post(path,json=body).status_code==403
    s.client.headers['Authorization']='Bearer '+'a'*32
    assert s.client.post(path,json={**body,'analysis_id':str(uuid4())}).status_code==404
    foreign = ActorContext('outsider','other-scope',s.actor.permissions)
    ref = s.artifacts.register(foreign,RegisterArtifact(b'sku,product,free_quantity,unit\nF,Foreign,0,pc\n','foreign.csv','text/csv',{},'foreign-reanalysis'))
    s.artifacts.set_usage_role(foreign,ref.artifact_id,'stock')
    profile = s.company.preview(foreign,ref.artifact_id)['profile']
    foreign_snapshot = s.company.save(foreign,None,profile,'foreign-profile')
    invalid_target = s.client.post(path,json={**body,'settings_snapshot_id':str(foreign_snapshot['id'])})
    assert invalid_target.status_code==404,invalid_target.text
    assert len(s.client.get(API).json()['runs'])==1
    assert s.service.run_once() is False


def test_search_page_snapshots_preserve_raw_responses_hash_refs_and_stage_versions(run_scenario):
    import hashlib
    import re
    s = run_scenario
    receipt,run = completed(s)
    repository = s.service.run_pipeline.repository
    archived = repository.source_pages(UUID(receipt['run_id']))
    assert len(archived)==len(run['search_pages'])==4
    ids = {row['page_id'] for row in archived}
    assert len(ids)==4
    assert all(re.fullmatch('[a-f0-9]{64}',identifier) for identifier in ids)
    assert set(run['data']['checkpoint']['page_refs'])==ids
    assert {p['page_id'] for p in run['search_pages']}==ids
    metadata = {p['page_id']:p for p in run['search_pages']}
    for row in archived:
        data = row['data']
        expected_hash = hashlib.sha256((receipt['run_id']+json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'))).encode()).hexdigest()
        assert row['page_id']==expected_hash
        response = data['response']
        assert type(response['TotalCount']) is int
        assert response['TotalCount']>0
        assert isinstance(response['Items'],list)
        assert len(response['Items'])==min(50,max(0,response['TotalCount']-data['page']*50))
        assert all(isinstance(item,dict) and item['Id'] for item in response['Items'])
        assert metadata[row['page_id']]==dict(page_id=row['page_id'],phrase=data['phrase'],page=data['page'],total_count=response['TotalCount'])
    versions = run['data']['stage_versions']
    assert versions['production']==4
    assert all(card['fulfillment']['version']==4 for card in run['cards'] if card.get('fulfillment'))
    assert all('stress' not in card['fulfillment'] for card in run['cards'] if card.get('fulfillment'))
    assert {'selection','normalization','matching','documents','production','score','review','audit'}==set(versions)
    assert all(isinstance(v,(str,int)) and v for v in versions.values())
    assert all(card['assessment']['policy_version']==versions['score'] for card in run['cards'])
    assert all(card['review']['prompt_version']==versions['review'] for card in run['cards'])
    assert all(card['tender']['document_extraction']['prompt_version']==versions['documents'] for card in run['cards'])
    assert repository.source_pages(UUID(receipt['run_id']))==archived


def _exact_http_price(s,monkeypatch,literal):
    handler = s.source.RequestHandlerClass
    reply = handler.reply
    marker = '__exact_price_on_wire__'
    def exact_reply(self,status,data):
        if status!=200 or not isinstance(data,dict) or not isinstance(data.get('Items'),list):
            return reply(self,status,data)
        data = deepcopy(data)
        for item in data['Items']:
            item['MaxPrice']=marker
        body = json.dumps(data,ensure_ascii=False).replace(json.dumps(marker),literal).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    monkeypatch.setattr(handler,'reply',exact_reply)


def test_fractional_external_money_survives_real_http_jsonb_checkpoints_without_float_rounding(run_scenario,monkeypatch):
    s = run_scenario
    literal = '1234.567890123456789012'
    _exact_http_price(s,monkeypatch,literal)
    receipt,run = completed(s)
    archived = s.service.run_pipeline.repository.source_pages(UUID(receipt['run_id']))
    assert len(archived)==4
    for row in archived:
        assert row['data']['response']['Items']
        assert {item['MaxPrice'] for item in row['data']['response']['Items']}=={literal}
    candidates = run['data']['checkpoint']['candidates']
    assert len(candidates)==160
    assert {candidate['brief']['MaxPrice'] for candidate in candidates}=={literal}
    def assert_no_floats(value):
        assert not isinstance(value,float)
        if isinstance(value,dict):
            for child in value.values(): assert_no_floats(child)
        elif isinstance(value,list):
            for child in value: assert_no_floats(child)
    for row in archived: assert_no_floats(row['data'])
    assert_no_floats(run['data']['checkpoint'])


def test_non_finite_external_price_is_rejected_before_archiving_or_jsonb_publication(run_scenario,monkeypatch):
    s = run_scenario
    _exact_http_price(s,monkeypatch,'NaN')
    receipt = enqueue(s)
    assert s.service.run_once()
    run = s.client.get(API+'/'+receipt['run_id']).json()
    assert run['data']['state']=='failed'
    assert run['data']['error']['code']=='KONTUR_INVALID_RESPONSE'
    assert run['data']['checkpoint']['search_attempts']==1
    assert run['cards']==[]
    assert run['search_pages']==[]
    assert s.service.run_pipeline.repository.source_pages(UUID(receipt['run_id']))==[]
