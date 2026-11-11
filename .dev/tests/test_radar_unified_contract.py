from copy import deepcopy
from uuid import UUID

from sqlalchemy import select

from test_ai_radar_runs import run_scenario,API
from test_ai_radar_audit import inputs
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
from bervel.core.models import ArtifactRef


def legacy_job(s,title='Existing tender',key='legacy',kontur_id=None):
    ref=UUID(s.snapshot['profile']['stock_artifact_id'])
    receipt=s.service.jobs.enqueue(s.actor,key,ArtifactRef(ref),'legacy',s.snapshot['profile']['stock_sha256'],None)
    claimed=s.service.jobs.claim()
    assert claimed['job_id']==receipt['job_id']
    card,_,_=inputs()
    analysis=card['analysis']
    with s.engine.begin() as c:
        c.execute(jobs.update().where(jobs.c.job_id==receipt['job_id']).values(state='loaded',kontur_id=kontur_id or key,
            tender={'id':kontur_id or key,'title':title,'number':key,'link':None,'position_count':1,'limitations':[]},
            analysis=analysis,snapshot={'tender':card['tender']},lease_until=None))
    return receipt['job_id']


def test_named_source_run_and_history_keep_command_identity(run_scenario):
    s=run_scenario
    body={'name':'Проверка склада','source':'test_api','settings_snapshot_id':s.snapshot['id'],'command_key':'named'}
    first=s.client.post(API,json=body)
    assert first.status_code==202,first.text
    replay=s.client.post(API,json=body)
    assert replay.json()['run_id']==first.json()['run_id']
    current=s.client.get(API+'/'+first.json()['run_id']).json()
    assert current['data']['name']==body['name']
    assert current['data']['source']=='test_api'
    assert current['data']['budgets']=={'search':None,'detail':None,'model':None}
    changed=s.client.post(API,json=dict(body,name='Другой прогон'))
    assert changed.status_code==409
    history=s.client.get(API+'/history')
    assert history.status_code==200,history.text
    assert history.json()['entries'][0]['title']==body['name']


def test_legacy_bundle_freezes_existing_visible_cards_without_rewriting(run_scenario):
    s=run_scenario
    first=legacy_job(s)
    with s.engine.connect() as c:before=dict(c.execute(select(jobs).where(jobs.c.job_id==first)).mappings().one())
    repository=s.service.run_pipeline.repository
    repository.freeze_legacy(s.actor.data_scope)
    repository.freeze_legacy(s.actor.data_scope)
    legacy_job(s,'Later tender','later')
    legacy_job(s,'New calculation of the same tender','later-copy',kontur_id='legacy')
    response=s.client.get(API+'/history')
    assert response.status_code==200,response.text
    entries=response.json()['entries']
    assert entries[0]['id']=='legacy'
    assert entries[0]['kind']=='legacy'
    assert entries[0]['card_count']==1
    legacy=s.client.get(API+'/legacy').json()
    assert [c['job_id'] for c in legacy['cards']]==[str(first)]
    assert s.client.get('/api/v0.1/ai-tender-radar/jobs/'+str(first)).status_code==200
    s.client.headers['Authorization']='Bearer '+'b'*32
    assert all(e['kind']!='legacy' for e in s.client.get(API+'/history').json()['entries'])
    with s.engine.connect() as c:after=dict(c.execute(select(jobs).where(jobs.c.job_id==first)).mappings().one())
    assert after==before


def test_additive_migration_freezes_current_visible_results(run_scenario):
    import importlib
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    from bervel.modules.ai_tender_radar.infrastructure.runs import legacy_groups
    s=run_scenario
    first=legacy_job(s)
    second=legacy_job(s,'Second','second')
    with s.engine.begin() as c:
        assert c.execute(text('SELECT current_schema()')).scalar_one().startswith('t_')
        legacy_groups.drop(c)
        with Operations.context(MigrationContext.configure(c)):
            importlib.import_module('bervel.core.migrations.versions.0027_radar_legacy_history').upgrade()
    repository=s.service.run_pipeline.repository
    entry=repository.legacy_entry(s.actor.data_scope)
    assert entry['card_count']==2
    assert {card['job_id'] for card in repository.legacy_cards(s.actor.data_scope)['cards']}=={first,second}


def test_source_and_name_errors_do_not_create_jobs_or_use_network(run_scenario):
    s=run_scenario
    body={'name':'   ','source':'test_api','settings_snapshot_id':s.snapshot['id'],'command_key':'bad-name'}
    assert s.client.post(API,json=body).status_code==422
    assert s.client.post(API,json=dict(body,name='Test',source='unknown')).status_code==422
    assert s.client.post(API,json=dict(body,name='File',source='file')).status_code==422
    before=deepcopy(s.source.remaining)
    s.service.run_pipeline.sources.pop('kontur_api',None)
    unavailable=s.client.post(API,json=dict(body,name='Live',source='kontur_api'))
    assert unavailable.status_code in (422,503),unavailable.text
    assert s.source.remaining==before
    assert s.client.get(API).json()['runs']==[]
