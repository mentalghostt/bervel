"""File selection is typed, scoped and reflects its own indexing readiness."""
from uuid import UUID
import pytest
from sqlalchemy import text
from bervel.core.errors import CoreError
from test_api import PREFIX, auth, client, upload


def test_file_list_has_role_and_real_index_status(client, pg_engine):
    http, keys, _=client
    ref=upload(http,keys[0]).json()
    item=http.get(PREFIX+'/artifacts',headers=auth(keys[0])).json()['artifacts'][0]
    assert item['usage_role']=='general'
    assert item['processing_state']=='pending'
    assert item['completed_chunks']==item['total_chunks']==0
    assert item['processing_error'] is None
    with pg_engine.begin() as c:
        c.execute(text("UPDATE knowledge_jobs SET state='ready',completed_chunks=2,total_chunks=2 WHERE artifact_id=:id"),{'id':UUID(ref['artifact_id'])})
    ready=http.get(PREFIX+'/artifacts',headers=auth(keys[0])).json()['artifacts'][0]
    assert ready['processing_state']=='ready' and ready['completed_chunks']==ready['total_chunks']==2


def test_role_changes_are_scoped_and_do_not_rewrite_original(client, pg_engine):
    http,keys,_=client
    ref=upload(http,keys[0]).json()
    path=PREFIX+f"/artifacts/{ref['artifact_id']}/usage-role"
    original=http.get(PREFIX+f"/artifacts/{ref['artifact_id']}/content",headers=auth(keys[0])).content
    assert http.put(path,json={'usage_role':'stock'}).status_code==401
    assert http.put(path,headers=auth(keys[2]),json={'usage_role':'stock'}).status_code==403
    assert http.put(path,headers=auth(keys[1]),json={'usage_role':'stock'}).status_code==404
    assert http.put(path,headers=auth(keys[0]),json={'usage_role':'unknown'}).status_code==422
    result=http.put(path,headers=auth(keys[0]),json={'usage_role':'stock'})
    assert result.status_code==200
    assert result.json()=={'artifact_id':ref['artifact_id'],'usage_role':'stock'}
    item=http.get(PREFIX+'/artifacts',headers=auth(keys[0])).json()['artifacts'][0]
    assert item['usage_role']=='stock'
    assert http.get(PREFIX+f"/artifacts/{ref['artifact_id']}/content",headers=auth(keys[0])).content==original
    assert len(http.get(PREFIX+'/artifacts',headers=auth(keys[0])).json()['artifacts'])==1


def test_prepared_metadata_checks_independent_files_and_deleted_source(services,pg_engine):
    from bervel.core.models import RegisterArtifact
    artifacts,_,_,actor=services
    ref=artifacts.register(actor,RegisterArtifact(b'one','one.txt','text/plain',{},'one'))
    artifacts.set_usage_role(actor,ref.artifact_id,'economics')
    with pytest.raises(CoreError,match='FILE_NOT_READY'):
        artifacts.prepared_metadata(actor,ref)
    with pg_engine.begin() as c:
        c.execute(text("UPDATE knowledge_jobs SET state='ready',total_chunks=1,completed_chunks=1 WHERE artifact_id=:id"),{'id':ref.artifact_id})
    assert artifacts.prepared_metadata(actor,ref)['usage_role']=='economics'
    second=artifacts.register(actor,RegisterArtifact(b'two','two.txt','text/plain',{},'two'))
    assert second.artifact_id != ref.artifact_id
    with pytest.raises(CoreError,match='FILE_NOT_READY'):
        artifacts.prepared_metadata(actor,second)
    assert artifacts.prepared_metadata(actor,ref)['processing_state']=='ready'
    artifacts.delete(actor,ref.artifact_id)
    with pytest.raises(CoreError,match='ARTIFACT_DELETED'):
        artifacts.prepared_metadata(actor,ref)
    with pytest.raises(CoreError,match='ARTIFACT_DELETED'):
        artifacts.set_usage_role(actor,ref.artifact_id,'stock')
    assert artifacts.read(actor,second).content==b'two'


@pytest.mark.parametrize('state',['partial','failed','unsupported','running','pending'])
def test_incomplete_jobs_are_never_prepared(services,pg_engine,state):
    from bervel.core.models import RegisterArtifact
    artifacts,_,_,actor=services
    ref=artifacts.register(actor,RegisterArtifact(b'one','one.txt','text/plain',{},'one'))
    with pg_engine.begin() as c:
        c.execute(text('UPDATE knowledge_jobs SET state=:state,total_chunks=2,completed_chunks=1 WHERE artifact_id=:id'),{'state':state,'id':ref.artifact_id})
    with pytest.raises(CoreError,match='FILE_NOT_READY'):
        artifacts.prepared_metadata(actor,ref)
