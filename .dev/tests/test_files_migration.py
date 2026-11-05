"""Upgrade real populated legacy schema without losing exact source identity."""
import json, os
from hashlib import sha256
from uuid import UUID, uuid4, uuid5
import pytest
from sqlalchemy import create_engine, text, inspect
from alembic import command
from alembic.config import Config
from importlib.resources import files

@pytest.fixture
def legacy_engine():
    admin=create_engine(os.environ['BERVEL_TEST_DATABASE_URL'])
    assert admin.url.database=='bervel_core_stage31_test'
    schema='t_'+uuid4().hex
    with admin.begin() as c: c.execute(text(f'CREATE SCHEMA {schema}'))
    engine=create_engine(admin.url.update_query_dict({'options':f'-csearch_path={schema}'}))
    config=Config();config.set_main_option('script_location',str(files('bervel.core').joinpath('migrations')))
    with engine.begin() as c:
        config.attributes['connection']=c;command.upgrade(config,'0012_assistant_workflows')
    yield engine
    engine.dispose()
    with admin.begin() as c:c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    admin.dispose()

def migrate_files_without_retirement(engine):
    """Test the historical file-identity migration before later feature retirement."""
    config = Config()
    config.set_main_option('script_location', str(files('bervel.core').joinpath('migrations')))
    with engine.begin() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, '0013_files_without_revisions')


def seed(engine):
    aid=UUID('10000000-0000-0000-0000-000000000001');dead=UUID('10000000-0000-0000-0000-000000000002')
    metadata=[]
    with engine.begin() as c:
        c.execute(text("INSERT INTO artifacts(artifact_id,data_scope,owner_module_id,current_revision) VALUES(:id,'scope','module',2)"),{'id':aid})
        c.execute(text("INSERT INTO artifacts(artifact_id,data_scope,owner_module_id,current_revision,deleted_at) VALUES(:id,'scope','module',1,now())"),{'id':dead})
        for rev in [1,2]:
            m=dict(artifact_id=str(aid),revision=rev,filename='same.txt',media_type='text/plain',source={},source_role='test_fixture',size=3,sha256=sha256(f'v{rev}!'.encode()).hexdigest(),object_ref=f'objects/{rev}',created_at='2026-10-28T00:00:00+00:00',data_scope='scope',owner_module_id='module')
            metadata.append(m)
            c.execute(text('INSERT INTO artifact_revisions VALUES(:id,:rev,CAST(:m AS jsonb))'),dict(id=aid,rev=rev,m=json.dumps(m)))
            c.execute(text("INSERT INTO knowledge_jobs(artifact_id,revision,state,total_chunks,completed_chunks) VALUES(:id,:rev,'ready',1,1)"),dict(id=aid,rev=rev))
            c.execute(text("INSERT INTO knowledge_chunks(artifact_id,revision,ordinal,version,source,embedding_input,content_hash,tokens,model,embedding) VALUES(:id,:rev,0,1,CAST(:s AS jsonb),'UNCHANGED INPUT','hash',2,'model',CAST('[1,2,3]' AS public.vector))"),dict(id=aid,rev=rev,s=json.dumps({'artifact_id':str(aid),'revision':rev,'excerpt':'revision 1 is raw text'})))
        session,turn=uuid4(),uuid4()
        c.execute(text("INSERT INTO chat_sessions VALUES(:id,'actor','scope',now())"),dict(id=session))
        src=dict(artifact_id=str(aid),revision=1,filename='same.txt')
        c.execute(text("INSERT INTO chat_turns(turn_id,session_id,ordinal,question,answer,state,snapshot,sources,actor_permissions,created_at,results) VALUES(:id,:session,1,'question revision 1','raw answer revision 1','completed',CAST(:s AS jsonb),CAST(:s AS jsonb),'[]',now(),CAST(:r AS jsonb))"),dict(id=turn,session=session,s=json.dumps([src]),r=json.dumps([{'kind':'file','artifact_id':str(aid),'revision':1}])))
    return aid,dead,metadata

def test_populated_upgrade_preserves_two_contents_vectors_and_deleted_identity(legacy_engine):
    from bervel.core.infrastructure.database import migrate
    aid,dead,metadata=seed(legacy_engine)
    migrate(legacy_engine)
    assert 'artifact_revisions' not in inspect(legacy_engine).get_table_names()
    old=uuid5(aid,'bervel:file-revision:1')
    with legacy_engine.connect() as c:
        rows=c.execute(text('SELECT artifact_id,metadata,deleted_at FROM artifacts ORDER BY artifact_id')).mappings().all()
        active={r['artifact_id']:r['metadata'] for r in rows if r['metadata']}
        assert set(active)=={old,aid}
        assert active[old]=={**{k:v for k,v in metadata[0].items() if k!='revision'},'artifact_id':str(old)}
        assert active[aid]=={k:v for k,v in metadata[1].items() if k!='revision'}
        assert next(r for r in rows if r['artifact_id']==dead)['deleted_at'] is not None
        chunks=c.execute(text('SELECT artifact_id,embedding::text,embedding_input,content_hash,model,source FROM knowledge_chunks')).mappings().all()
        assert {r['artifact_id'] for r in chunks}=={old,aid}
        assert all(r['embedding']=='[1,2,3]' and r['embedding_input']=='UNCHANGED INPUT' and r['content_hash']=='hash' and r['model']=='model' for r in chunks)
        assert all('revision' not in r['source'] and r['source']['excerpt']=='revision 1 is raw text' for r in chunks)
        turn=c.execute(text('SELECT * FROM chat_turns')).mappings().one()
        assert turn['answer']=='raw answer revision 1'
        assert turn['snapshot']==[{'artifact_id':str(old),'filename':'same.txt'}]
        assert turn['results']==[{'kind':'file','artifact_id':str(old)}]

def test_upgrade_remaps_relational_and_json_evidence_but_preserves_business_versions(legacy_engine):
    from bervel.core.infrastructure.database import migrate
    aid,dead,metadata=seed(legacy_engine)
    old=uuid5(aid,'bervel:file-revision:1')
    batch,run,dataset,snapshot,process,action=[uuid4() for _ in range(6)]
    source={'artifact_id':str(aid),'revision':1}
    business={'artifact_id':str(aid),'revision':71,'price':'12.34'}
    with legacy_engine.begin() as c:
        c.execute(text("INSERT INTO radar_batches VALUES(:id,'scope','actor',:aid,1,'same.txt',CAST(:data AS jsonb),now())"),dict(id=batch,aid=aid,data=json.dumps({'records':[business]})))
        report={'sources':[source],'batch':source,'selection':{'stock_source':source},'items':[business],'schema_version':'1.3.0'}
        c.execute(text("INSERT INTO radar_runs(run_id,batch_id,actor_id,data_scope,actor_permissions,sources_snapshot,focus,state,created_at,report,selection) VALUES(:id,:batch,'actor','scope','[]',CAST(:s AS jsonb),'focus','completed',now(),CAST(:report AS jsonb),CAST(:sel AS jsonb))"),dict(id=run,batch=batch,s=json.dumps([source]),report=json.dumps(report),sel=json.dumps({'stock_source':source})))
        c.execute(text("INSERT INTO schema_definitions VALUES('schema','1.2','checksum','{}')"))
        c.execute(text("INSERT INTO datasets VALUES(:id,:aid,'scope','module',7)"),dict(id=dataset,aid=aid))
        c.execute(text("INSERT INTO input_snapshots VALUES(:id,'scope',CAST(:s AS jsonb))"),dict(id=snapshot,s=json.dumps([source])))
        c.execute(text("INSERT INTO process_runs VALUES(:id,'scope',:snapshot,CAST(:producer AS jsonb),8,'COMPLETED',now())"),dict(id=process,snapshot=snapshot,producer=json.dumps({'revision':9})))
        c.execute(text("INSERT INTO dataset_revisions VALUES(:id,7,:aid,1,:snapshot,:process,'schema','1.2',CAST(:e AS jsonb),1)"),dict(id=dataset,aid=aid,snapshot=snapshot,process=process,e=json.dumps({'revision':7,'configuration_revision':8,'input_snapshot':{'inputs':[source]},'payload':business})))
        c.execute(text("INSERT INTO data_records VALUES(:id,7,0,'record',CAST(:r AS jsonb))"),dict(id=dataset,r=json.dumps({'payload':business,'evidence_refs':[source]})))
        c.execute(text("INSERT INTO chat_actions VALUES(:id,:turn,0,'actor','scope','file.read',CAST(:args AS jsonb),'old','completed',CAST(:result AS jsonb),NULL,now(),now())"),dict(id=action,turn=uuid4(),args=json.dumps(source),result=json.dumps({'kind':'data','operation':'file.read','data':{'file':source,'sources':[source]}})))
    migrate_files_without_retirement(legacy_engine)
    with legacy_engine.connect() as c:
        b=c.execute(text('SELECT * FROM radar_batches')).mappings().one()
        assert b['artifact_id']==old and b['data']=={'records':[business]}
        r=c.execute(text('SELECT * FROM radar_runs')).mappings().one()
        assert r['report']['sources']==[{'artifact_id':str(old)}]
        assert r['report']['items']==[business] and r['report']['schema_version']=='1.3.0'
        d=c.execute(text('SELECT * FROM dataset_revisions')).mappings().one()
        assert d['artifact_id']==old and d['revision']==7
        assert d['envelope']['input_snapshot']['inputs']==[{'artifact_id':str(old)}]
        assert d['envelope']['revision']==7 and d['envelope']['configuration_revision']==8 and d['envelope']['payload']==business
        record=c.execute(text('SELECT record FROM data_records')).scalar_one()
        assert record=={'payload':business,'evidence_refs':[{'artifact_id':str(old)}]}
        a=c.execute(text('SELECT * FROM chat_actions')).mappings().one()
        assert a['arguments']=={'artifact_id':str(old)} and a['state']=='completed'
        assert a['result']['data']=={'file':{'artifact_id':str(old)},'sources':[{'artifact_id':str(old)}]}


def test_purged_older_source_stays_gone_and_batch_uses_same_identity(legacy_engine):
    from bervel.core.infrastructure.database import migrate
    from bervel.core.infrastructure.repository import PostgresRepository
    from bervel.core.models import ActorContext, ArtifactRef
    from bervel.core.errors import CoreError
    aid,dead,_=seed(legacy_engine)
    with legacy_engine.begin() as c:
        c.execute(text('UPDATE artifacts SET current_revision=2 WHERE artifact_id=:id'),{'id':dead})
        c.execute(text("INSERT INTO radar_batches VALUES(:id,'scope','actor',:aid,1,'deleted.txt','{}',now())"),dict(id=uuid4(),aid=dead))
        c.execute(text("UPDATE chat_turns SET sources=CAST(:source AS jsonb)"),dict(source=json.dumps([{'artifact_id':str(dead),'revision':1}])))
    migrate_files_without_retirement(legacy_engine)
    target=uuid5(dead,'bervel:file-revision:1')
    actor=ActorContext('actor','scope',frozenset({'artifact:read'}))
    with pytest.raises(CoreError,match='ARTIFACT_DELETED'):
        PostgresRepository(legacy_engine).read_artifact(actor,ArtifactRef(target))
    with legacy_engine.connect() as c:
        assert c.execute(text('SELECT artifact_id FROM radar_batches')).scalar_one()==target
        assert c.execute(text('SELECT sources FROM chat_turns')).scalar_one()==[{'artifact_id':str(target)}]

def test_migrated_upload_and_action_receipts_replay_original_and_reject_changed_payload(legacy_engine, tmp_path):
    from bervel.core.infrastructure.database import migrate
    from bervel.core.infrastructure.commands import digest
    from bervel.core.infrastructure.repository import PostgresRepository
    from bervel.core.infrastructure.storage import FileObjectStore
    from bervel.core.services import ArtifactService
    from bervel.core.models import ActorContext, RegisterArtifact
    from bervel.core.errors import CoreError
    from bervel.modules.chat.infrastructure.actions import PostgresActionReceipts
    aid,_,metadata=seed(legacy_engine)
    old=uuid5(aid,'bervel:file-revision:1')
    significant={k:metadata[0][k] for k in ('sha256','size','filename','media_type','source','source_role')}
    oldresult={'artifact_id':str(aid),'revision':1}
    turn=uuid4()
    with legacy_engine.begin() as c:
        c.execute(text("INSERT INTO command_receipts VALUES('scope','actor','artifact.register','upload',:fingerprint,CAST(:result AS jsonb))"),dict(fingerprint=digest({**significant,'artifact_id':None,'expected_revision':0}),result=json.dumps(oldresult)))
        c.execute(text("INSERT INTO audit VALUES(:id,'scope','actor','artifact.register',CAST(:result AS jsonb),now())"),dict(id=uuid4(),result=json.dumps(oldresult)))
        c.execute(text("INSERT INTO chat_actions VALUES(:id,:turn,0,'actor','scope','file.read',CAST(:args AS jsonb),:fingerprint,'completed',CAST(:result AS jsonb),NULL,now(),now())"),dict(id=uuid4(),turn=turn,args=json.dumps(oldresult),fingerprint=digest({'operation':'file.read','arguments':oldresult}),result=json.dumps({'kind':'data','operation':'file.read','data':{'file':oldresult,'sources':[oldresult]}})))
    migrate(legacy_engine)
    service=ArtifactService(PostgresRepository(legacy_engine), FileObjectStore(tmp_path/'objects'))
    actor=ActorContext('actor','scope',frozenset({'artifact:write','artifact:read'}))
    command=RegisterArtifact(b'v1!','same.txt','text/plain',{},'upload')
    assert service.register(actor,command).artifact_id==old
    from dataclasses import replace
    with pytest.raises(CoreError,match='IDEMPOTENCY_CONFLICT'):
        service.register(actor,replace(command,content=b'v2!'))
    receipt=PostgresActionReceipts(legacy_engine).begin(actor,turn,0,'file.read',{'artifact_id':str(old)})
    assert receipt['state']=='completed' and receipt['result']['data']['file']=={'artifact_id':str(old)}
    with pytest.raises(CoreError,match='IDEMPOTENCY_CONFLICT'):
        PostgresActionReceipts(legacy_engine).begin(actor,turn,0,'file.read',{'artifact_id':str(aid)})
    with legacy_engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM artifacts')).scalar_one()==3
        assert c.execute(text('SELECT object_ref FROM audit')).scalar_one()=={'artifact_id':str(old)}


def test_cached_document_sources_and_retired_process_command_keep_exact_file(legacy_engine):
    from bervel.core.infrastructure.database import migrate
    aid,_,_=seed(legacy_engine)
    source={'artifact_id':str(aid),'revision':1}
    old=uuid5(aid,'bervel:file-revision:1')
    with legacy_engine.begin() as c:
        c.execute(text('INSERT INTO document_indexes VALUES(:id,1,5,CAST(:result AS jsonb))'),dict(id=aid,result=json.dumps({'sources':[source],'chunks':[{'source':source,'excerpt':'raw revision 1'}]})))
        snapshot,run=uuid4(),uuid4()
        c.execute(text("INSERT INTO input_snapshots VALUES(:id,'scope',CAST(:refs AS jsonb))"),dict(id=snapshot,refs=json.dumps([source])))
        c.execute(text("INSERT INTO process_runs VALUES(:id,'scope',:snapshot,'{}',1,'QUEUED',now())"),dict(id=run,snapshot=snapshot))
        c.execute(text("INSERT INTO process_jobs VALUES(:id,'actor','[]',CAST(:command AS jsonb),'{}',1,NULL,now())"),dict(id=run,command=json.dumps({'artifact_ref':source,'configuration_revision':17})))
    migrate(legacy_engine)
    with legacy_engine.connect() as c:
        cached=c.execute(text('SELECT result FROM document_indexes')).scalar_one()
        assert cached=={'sources':[{'artifact_id':str(old)}],'chunks':[{'source':{'artifact_id':str(old)},'excerpt':'raw revision 1'}]}
        command=c.execute(text('SELECT command FROM process_jobs')).scalar_one()
        assert command=={'artifact_ref':{'artifact_id':str(old)},'configuration_revision':17}

def test_migration_removes_retired_radar_actions_and_keeps_ticket_revision(legacy_engine):
    from bervel.core.infrastructure.database import migrate
    aid,_,_=seed(legacy_engine)
    source={'artifact_id':str(aid),'revision':1}
    arguments={'procurement':source,'stock':source,'economics':source,'filters':{'revision':22}}
    ticket={'ticket_id':str(uuid4()),'expected_revision':8,'title':'revision 1'}
    with legacy_engine.begin() as c:
        for n,(operation,args) in enumerate([('radar.analyse',arguments),('ticket.edit',ticket)]):
            c.execute(text("INSERT INTO chat_actions VALUES(:id,:turn,:n,'actor','scope',:op,CAST(:args AS jsonb),'old','completed',NULL,NULL,now(),now())"),dict(id=uuid4(),turn=uuid4(),n=n,op=operation,args=json.dumps(args)))
    migrate(legacy_engine)
    with legacy_engine.connect() as c:
        rows={r['operation']:r['arguments'] for r in c.execute(text('SELECT operation,arguments FROM chat_actions')).mappings()}
    assert 'radar.analyse' not in rows
    assert rows['ticket.edit']==ticket

def test_stored_file_card_preview_and_mixed_dataset_version_survive(legacy_engine):
    from bervel.core.infrastructure.database import migrate
    aid,_,_=seed(legacy_engine)
    path=f'/api/v0.1/artifacts/{aid}/revisions/1'
    card={'kind':'file','artifact_id':str(aid),'revision':1,'preview_url':path+'/preview','download_url':path+'/content'}
    with legacy_engine.begin() as c:
        c.execute(text('UPDATE chat_turns SET results=CAST(:r AS jsonb)'),dict(r=json.dumps([card])))
    migrate(legacy_engine)
    old=uuid5(aid,'bervel:file-revision:1')
    with legacy_engine.connect() as c:
        result=c.execute(text('SELECT results FROM chat_turns')).scalar_one()[0]
    assert result['preview_url']==f'/api/v0.1/artifacts/{old}/preview'
    assert result['download_url']==f'/api/v0.1/artifacts/{old}/content'


def test_dataset_envelope_own_revision_is_not_a_file_revision(legacy_engine):
    from bervel.core.infrastructure.database import migrate
    aid,_,_=seed(legacy_engine)
    dataset,snapshot,process=uuid4(),uuid4(),uuid4()
    mixed={'dataset_id':str(dataset),'revision':7,'artifact_id':str(aid),'artifact_revision':1,'configuration_revision':8,'expected_revision':0}
    with legacy_engine.begin() as c:
        c.execute(text("INSERT INTO schema_definitions VALUES('schema','1.2','checksum','{}')"))
        c.execute(text("INSERT INTO datasets VALUES(:id,:aid,'scope','module',7)"),dict(id=dataset,aid=aid))
        c.execute(text("INSERT INTO input_snapshots VALUES(:id,'scope','[]')"),dict(id=snapshot))
        c.execute(text("INSERT INTO process_runs VALUES(:id,'scope',:snapshot,'{}',8,'COMPLETED',now())"),dict(id=process,snapshot=snapshot))
        c.execute(text("INSERT INTO dataset_revisions VALUES(:id,7,:aid,1,:snapshot,:process,'schema','1.2',CAST(:e AS jsonb),1)"),dict(id=dataset,aid=aid,snapshot=snapshot,process=process,e=json.dumps(mixed)))
    migrate(legacy_engine)
    with legacy_engine.connect() as c:
        envelope=c.execute(text('SELECT envelope FROM dataset_revisions')).scalar_one()
    assert envelope=={'dataset_id':str(dataset),'revision':7,'artifact_id':str(uuid5(aid,'bervel:file-revision:1')),'configuration_revision':8,'expected_revision':0}

def test_process_commands_preserve_dataset_expected_revision_zero_even_with_null_dataset(legacy_engine):
    from bervel.core.infrastructure.database import migrate
    aid,_,_=seed(legacy_engine)
    commands=[
        {'dataset_id':None,'artifact_id':'81c813f3-68e2-48b0-860a-cbc727d80632','module_version':'1.0.0','artifact_revision':1,'expected_revision':0},
        {'dataset_id':None,'artifact_id':str(aid),'module_version':'1.0.0','artifact_revision':1,'expected_revision':0},
    ]
    with legacy_engine.begin() as c:
        c.execute(text("INSERT INTO artifacts(artifact_id,data_scope,owner_module_id,current_revision,deleted_at) VALUES(:id,'scope','module',1,now())"),{'id':UUID(commands[0]['artifact_id'])})
        for value in commands:
            snapshot,run=uuid4(),uuid4()
            c.execute(text("INSERT INTO input_snapshots VALUES(:id,'scope','[]')"),dict(id=snapshot))
            c.execute(text("INSERT INTO process_runs VALUES(:id,'scope',:snapshot,'{}',1,'QUEUED',now())"),dict(id=run,snapshot=snapshot))
            c.execute(text("INSERT INTO process_jobs VALUES(:id,'actor','[]',CAST(:command AS jsonb),'{}',1,NULL,now())"),dict(id=run,command=json.dumps(value)))
    migrate(legacy_engine)
    with legacy_engine.connect() as c:
        actual=c.execute(text('SELECT command FROM process_jobs')).scalars().all()
    assert {json.dumps(c,sort_keys=True) for c in actual}=={
        json.dumps({'dataset_id':None,'artifact_id':'81c813f3-68e2-48b0-860a-cbc727d80632','module_version':'1.0.0','expected_revision':0},sort_keys=True),
        json.dumps({'dataset_id':None,'artifact_id':str(uuid5(aid,'bervel:file-revision:1')),'module_version':'1.0.0','expected_revision':0},sort_keys=True),
    }
