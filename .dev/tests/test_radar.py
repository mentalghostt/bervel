from bervel.core.infrastructure.documents import PostgresDocumentRepository
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4
import json
import pytest
from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact, ArtifactRef
from bervel.modules.tender_radar.service import RadarService
from bervel.modules.tender_radar.infrastructure.repository import PostgresRadarRepository
from bervel.modules.tender_radar.infrastructure.importer import TenderImporter
from bervel.core.document_memory import DocumentMemory
from bervel.core.infrastructure.document_reader import DocumentReader
from test_tender_importer import normalized, xlsx

class Model:
    def __init__(self): self.calls=[]
    def generate(self, request_id, prompt, **kwargs):
        self.calls.append(prompt)
        data=json.loads(prompt.split('\n',1)[1])
        index = next(i for i, source in enumerate(data['SELECTED_SOURCES']) if 'Болт' in source['excerpt'])
        return {'model':'test-model','answer':json.dumps({'items':[{'id':row['id'],'fit':'relevant','reason':'Болты соответствуют профилю группы; производство неизвестно.',
            'evidence_indexes':[index],'product_group':'Болт','tender_quote':row['title'][:160],'profile_quote':'Болт',
            'risks':['Проверить ТЗ'],'next_steps':['Получить ТЗ'],'positions':[],'positions_complete':False} for row in data['TENDERS']]})}

@pytest.fixture
def radar(services,pg_engine):
    artifacts,_,_,actor=services
    actor=replace(actor,permissions=actor.permissions|{'process:start','process:read'})
    repo=PostgresRadarRepository(pg_engine)
    model=Model()
    return RadarService(repo,artifacts,DocumentMemory(artifacts,PostgresDocumentRepository(pg_engine),DocumentReader()),TenderImporter(),model,
        now=lambda:datetime(2026,10,22,12,tzinfo=timezone.utc)),artifacts,actor,repo,model

def add(artifacts,actor,name,content,**kwargs):
    return artifacts.register(actor,RegisterArtifact(content,name,'application/octet-stream',{},uuid4().hex,**kwargs))

def test_selected_sources_pinned_and_unselected_files_never_enter_model(radar):
    service,artifacts,actor,_,model=radar
    raw=add(artifacts,actor,'t.csv',normalized())
    batch=service.import_batch(actor,raw,'import')
    assert service.import_batch(actor,raw,'import')['batch_id']==batch['batch_id']
    selected=add(artifacts,actor,'profile.txt','GOST7798 bolt M16. Bolts. PRIVATE_SELECTED_514. Болт ГОСТ7798'.encode())
    add(artifacts,actor,'unselected.txt',b'FORBIDDEN_UNSELECTED_913')
    queued=service.start(actor,UUID(batch['batch_id']),[selected],'','start')
    add(artifacts,actor,'profile.txt',b'NEW_FILE_MUST_NOT_APPEAR_934')
    assert service.start(actor,UUID(batch['batch_id']),[selected],'','start')['run_id']==queued['run_id']
    assert service.run_once()
    result=service.read(actor,UUID(queued['run_id']))
    assert result['state']=='completed' and result['report']['counts']['needs_review']==1
    assert 'PRIVATE_SELECTED_514' in model.calls[0]
    assert 'FORBIDDEN_UNSELECTED_913' not in model.calls[0] and 'NEW_FILE_MUST_NOT_APPEAR_934' not in model.calls[0]
    assert result['sources_snapshot'][0]['artifact_id']==str(selected.artifact_id)
    assert 'revision' not in result['sources_snapshot'][0]
    assert not service.run_once()

def test_scope_and_permissions_cannot_be_bypassed_by_references(radar):
    service,artifacts,actor,_,_=radar
    other=replace(actor,data_scope='foreign')
    ref=add(artifacts,other,'foreign.txt',b'FORBIDDEN')
    batch=service.import_batch(actor,add(artifacts,actor,'t.csv',normalized()),'i')
    with pytest.raises(CoreError): service.start(actor,UUID(batch['batch_id']),[ref],'','x')
    with pytest.raises(CoreError): service.import_batch(other,ArtifactRef(UUID(batch['artifact_id'])),'z')
    with pytest.raises(CoreError): service.list_batches(replace(actor,permissions=frozenset()))

def test_interrupted_model_execution_is_not_automatically_repeated(radar):
    service,artifacts,actor,repo,model=radar
    batch=service.import_batch(actor,add(artifacts,actor,'t.csv',normalized()),'i')
    ref=add(artifacts,actor,'profile.txt','Болт ГОСТ7798 М16'.encode())
    run=service.start(actor,UUID(batch['batch_id']),[ref],'','r')
    with repo.claim() as claimed: assert str(claimed['run_id'])==run['run_id']
    assert service.run_once()
    assert service.read(actor,UUID(run['run_id']))['state']=='failed'
    assert model.calls==[]

def test_office_documents_are_readable_for_selected_rag():
    reader=DocumentReader()
    parsed=reader.extract(xlsx(),'profile.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    assert parsed['status']=='ready' and 'Поставка болтов' in ''.join(c['excerpt'] for c in parsed['chunks'])


def test_malformed_office_xml_stays_metadata_only_instead_of_breaking_memory():
    import io
    from zipfile import ZipFile
    stream=io.BytesIO()
    with ZipFile(stream,'w') as archive: archive.writestr('word/document.xml','<broken')
    parsed=DocumentReader().extract(stream.getvalue(),'bad.docx','application/octet-stream')
    assert parsed['status']=='metadata_only'


@pytest.mark.parametrize('text',['','x'*15000])
def test_empty_and_truncated_docx_are_honest(text):
    import io
    from zipfile import ZipFile
    stream=io.BytesIO()
    with ZipFile(stream,'w') as archive: archive.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:body></w:document>')
    parsed=DocumentReader().extract(stream.getvalue(),'profile.docx','application/octet-stream')
    assert parsed['status']==('metadata_only' if not text else 'partial')


def test_http_contract_validates_inputs_permissions_and_persists_run(radar,pg_engine,tmp_path):
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from http_client import ASGITestClient
    _,_,actor,_,_=radar
    settings=Settings(pg_engine.url.render_as_string(hide_password=False),tmp_path/'objects',{'a'*32:actor,'b'*32:replace(actor,permissions=actor.permissions-{'process:start'})})
    headers={'Authorization':'Bearer '+'a'*32}
    with ASGITestClient(create_app(settings)) as http:
        assert http.get('/api/v0.1/radar/batches').status_code==401
        upload=http.post('/api/v0.1/artifacts',headers=headers,data={'command_key':'up'},files={'file':('t.csv',normalized(),'text/csv')})
        assert upload.status_code==200
        assert set(upload.json()) == {'artifact_id'}
        assert http.post('/api/v0.1/radar/batches',headers=headers,
            json={**upload.json(),'revision':1,'command_key':'obsolete'}).status_code==422
        batch=http.post('/api/v0.1/radar/batches',headers=headers,json={**upload.json(),'command_key':'import'})
        assert batch.status_code==200 and batch.json()['count']==1
        assert 'artifact_revision' not in batch.json()
        body={'command_key':'start','batch_id':batch.json()['batch_id'],'sources':[upload.json()],'focus':''}
        assert http.post('/api/v0.1/radar/runs',headers={'Authorization':'Bearer '+'b'*32},json=body).status_code==403
        assert http.post('/api/v0.1/radar/runs',headers=headers,json={**body,'sources':[]}).status_code==422
        assert http.post('/api/v0.1/radar/runs',headers=headers,
            json={**body,'sources':[{**upload.json(),'revision':1}]}).status_code==422
        run=http.post('/api/v0.1/radar/runs',headers=headers,json=body)
        assert run.status_code==202 and run.json()['state']=='queued'
        assert http.get('/api/v0.1/radar/runs/'+run.json()['run_id'],headers=headers).json()==run.json()


def test_concurrent_repeated_start_and_claim_do_not_duplicate_model(radar):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    service,artifacts,actor,_,model=radar
    batch=service.import_batch(actor,add(artifacts,actor,'t.csv',normalized()),'i')
    ref=add(artifacts,actor,'profile.txt','Болт М16'.encode())
    with ThreadPoolExecutor(3) as pool:
        started=list(pool.map(lambda _:service.start(actor,UUID(batch['batch_id']),[ref],'','same'),range(3)))
    assert len({run['run_id'] for run in started})==1
    entered,release=Event(),Event();generate=model.generate
    def blocked(*args,**kwargs):
        entered.set();assert release.wait(5);return generate(*args,**kwargs)
    model.generate=blocked
    with ThreadPoolExecutor(2) as pool:
        running=pool.submit(service.run_once)
        assert entered.wait(5)
        assert not service.run_once()
        release.set();assert running.result()
    assert len(model.calls)==1


def test_radar_explicit_selection_is_not_overridden_by_latest_csv_phrase(radar):
    service,artifacts,actor,_,model=radar
    batch=service.import_batch(actor,add(artifacts,actor,'t.csv',normalized()),'i')
    first=add(artifacts,actor,'profile.csv',b'product,standard\nbolt,GOST7798\n')
    limits=add(artifacts,actor,'limits.txt','LIMIT_SELECTED_624. Болты М16. Нужна проверка покрытия.'.encode())
    run=service.start(actor,UUID(batch['batch_id']),[first,limits],'Болты из последнего CSV с учётом ограничений','s')
    service.run_once()
    assert service.read(actor,UUID(run['run_id']))['state']=='completed'
    assert 'LIMIT_SELECTED_624' in model.calls[0]
