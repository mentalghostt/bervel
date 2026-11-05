"""Deletion removes only owned data; shared bytes and historical results survive."""
import json
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import text

from bervel.core.errors import CoreError
from test_api import PREFIX, auth, client, upload
from test_knowledge import setup, upload as register


def rows(engine, table):
    with engine.connect() as c:
        return c.execute(text(f'SELECT row_to_json(t)::text FROM {table} t ORDER BY row_to_json(t)::text')).scalars().all()


def test_delete_owned_indexes_and_unique_bytes_only(services, pg_engine):
    knowledge, indexes, _, artifacts, actor = setup(services, pg_engine)
    _, _, store, _ = services
    first = register(artifacts, actor, 'Болт М16')
    second = register(artifacts, actor, 'Болт М20')
    other = register(artifacts, actor, 'Болт М24')
    foreign = register(artifacts, replace(actor, data_scope='other'), 'Болт М30')
    while knowledge.run_once():
        pass
    originals = [artifacts.metadata(actor, first)]
    with pg_engine.begin() as c:
        for ref in (first, second, other):
            c.execute(text("INSERT INTO document_indexes VALUES(:id,5,CAST(:value AS jsonb)) ON CONFLICT (artifact_id) DO UPDATE SET result=EXCLUDED.result"),
                      dict(id=ref.artifact_id, value=json.dumps({'chunks': ['owned']})))
            c.execute(text("INSERT INTO csv_chunks VALUES(:id,0,1,1,1,'owned')"), dict(id=ref.artifact_id))
    untouched = {t: rows(pg_engine, t) for t in ('command_receipts', 'audit', 'input_snapshots', 'process_runs', 'schema_definitions')}
    with pg_engine.connect() as c:
        before_other = {t: c.execute(text(f'SELECT row_to_json(t)::text FROM {t} t WHERE artifact_id=:id ORDER BY row_to_json(t)::text'), {'id':other.artifact_id}).scalars().all()
                        for t in ('knowledge_jobs', 'knowledge_chunks', 'document_indexes', 'csv_chunks')}
    artifacts.delete(actor, first.artifact_id)
    assert {r['artifact_id'] for r in artifacts.list(actor)['artifacts']} == {str(second.artifact_id),str(other.artifact_id)}
    assert artifacts.read(actor, second).content == 'Болт М20'.encode()
    assert artifacts.read(actor, other).content == 'Болт М24'.encode()
    assert artifacts.read(replace(actor, data_scope='other'), foreign).content == 'Болт М30'.encode()
    for metadata in originals:
        digest = metadata['sha256']
        assert not (store.root / digest[:2] / digest).exists()
    with pg_engine.connect() as c:
        for table in before_other:
            assert c.execute(text(f'SELECT count(*) FROM {table} WHERE artifact_id=:id'), {'id':first.artifact_id}).scalar() == 0
            assert c.execute(text(f'SELECT row_to_json(t)::text FROM {table} t WHERE artifact_id=:id ORDER BY row_to_json(t)::text'), {'id':other.artifact_id}).scalars().all() == before_other[table]
    assert {t: rows(pg_engine, t) for t in untouched} == untouched
    with pytest.raises(CoreError, match='ARTIFACT_DELETED'):
        artifacts.read(actor, first)
    assert knowledge.run_once() is False


def test_shared_object_survives_across_files_and_scopes(services, pg_engine):
    artifacts, _, store, actor = services
    first = register(artifacts, actor, 'same bytes')
    second = register(artifacts, actor, 'same bytes')
    foreign_actor = replace(actor, data_scope='foreign')
    foreign = register(artifacts, foreign_actor, 'same bytes')
    digest = sha256(b'same bytes').hexdigest()
    target = store.root / digest[:2] / digest
    artifacts.delete(actor, first.artifact_id)
    assert artifacts.read(actor, second).content == b'same bytes'
    assert artifacts.read(foreign_actor, foreign).content == b'same bytes'
    artifacts.delete(actor, second.artifact_id)
    assert target.exists()
    artifacts.delete(foreign_actor, foreign.artifact_id)
    assert not target.exists()


def test_http_deleted_sources_report_gone_without_disclosing_to_foreign_scope(client):
    http, keys, _ = client
    first = upload(http, keys[0]).json()
    path = PREFIX + f"/artifacts/{first['artifact_id']}"
    assert http.delete(path).status_code == 401
    assert http.delete(path, headers=auth(keys[2])).status_code == 403
    assert http.delete(path, headers=auth(keys[1])).status_code == 404
    assert http.delete(path+'?expected_revision=2', headers=auth(keys[0])).status_code == 422
    assert http.get(path+'/content', headers=auth(keys[0])).status_code == 200
    assert http.delete(path, headers=auth(keys[0])).status_code == 204
    assert http.delete(path, headers=auth(keys[0])).status_code == 204
    for suffix in ('', '/content', '/preview', '/knowledge'):
        response = http.get(path+suffix, headers=auth(keys[0]))
        assert response.status_code == 410, (suffix, response.text)
        assert response.json()['error'] == {'code':'ARTIFACT_DELETED', 'message':'Файл удалён.'}
        assert http.get(path+suffix, headers=auth(keys[1])).status_code == 404
    assert upload(http, keys[0], 2, f"/artifacts/{first['artifact_id']}/revisions", expected_revision='1').status_code == 404
    assert upload(http, keys[0]).status_code == 410  # A late retry cannot recreate a deleted upload.


def test_delete_preserves_chat_and_ai_radar_snapshots_byte_for_byte(services, pg_engine):
    artifacts, _, _, actor = services
    ref = register(artifacts, actor)
    session, turn, job = [uuid4() for _ in range(3)]
    source = {'artifact_id':str(ref.artifact_id),'filename':'profile.txt','excerpt':'Сохранённая цитата'}
    with pg_engine.begin() as c:
        c.execute(text("INSERT INTO chat_sessions VALUES(:id,:actor,:scope,now())"), dict(id=session, actor=actor.actor_id, scope=actor.data_scope))
        c.execute(text("""INSERT INTO chat_turns(turn_id,session_id,ordinal,question,answer,state,snapshot,sources,actor_permissions,created_at)
            VALUES(:id,:session,1,'question','Сохранённый ответ','completed',CAST(:sources AS jsonb),CAST(:sources AS jsonb),'[]',now())"""),
                  dict(id=turn, session=session, sources=json.dumps([source])))
        c.execute(text("""INSERT INTO ai_tender_radar_jobs(job_id,data_scope,actor_id,command_key,
            stock_artifact_id,stock_sha256,search_phrase,state,stock,tender,snapshot,analysis)
            VALUES(:id,:scope,:actor,'preserved-ai-job',:artifact,repeat('a',64),'болт','loaded',
                CAST(:sources AS jsonb),CAST(:sources AS jsonb),CAST(:sources AS jsonb),CAST(:report AS jsonb))"""),
                  dict(id=job, actor=actor.actor_id, scope=actor.data_scope, artifact=ref.artifact_id,
                      sources=json.dumps([source]), report=json.dumps({'sources':[source],'result':'Сохранённый отчёт'})))
    before = {table:rows(pg_engine, table) for table in ('chat_sessions','chat_turns','ai_tender_radar_jobs')}
    artifacts.delete(actor, ref.artifact_id)
    assert {table:rows(pg_engine, table) for table in before} == before


def test_storage_failure_is_retryable_and_never_reports_success(services, pg_engine, monkeypatch):
    artifacts, _, store, actor = services
    ref = register(artifacts, actor, 'unique pending deletion')
    original_delete = store.delete
    def unavailable(object_ref):
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Cannot remove object')
    monkeypatch.setattr(store, 'delete', unavailable)
    with pytest.raises(CoreError, match='ARTIFACT_CLEANUP_PENDING'):
        artifacts.delete(actor, ref.artifact_id)
    assert artifacts.list(actor)['artifacts'] == []
    monkeypatch.setattr(store, 'delete', original_delete)
    assert artifacts.cleanup_deleted() is True
    assert rows(pg_engine, 'artifact_object_deletions') == []
    assert not [p for p in store.root.rglob('*') if p.is_file()]


def test_concurrent_upload_waits_until_shared_object_cleanup_finishes(services, pg_engine, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    artifacts, repository, store, actor = services
    ref = register(artifacts, actor, 'same bytes')
    started, completed, removing, release = Event(), Event(), Event(), Event()
    original_delete = store.delete
    def slow_delete(object_ref):
        removing.set()
        assert release.wait(5)
        original_delete(object_ref)
    monkeypatch.setattr(store, 'delete', slow_delete)
    def upload_again():
        started.set()
        result = register(artifacts, actor, 'same bytes')
        completed.set()
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(artifacts.delete, actor, ref.artifact_id)
        try:
            assert removing.wait(3)
            waiting = pool.submit(upload_again)
            assert started.wait(2)
            assert not completed.wait(0.15)
        finally:
            release.set()
        deletion.result(timeout=5)
        other = waiting.result(timeout=5)
    assert artifacts.read(actor, other).content == b'same bytes'


def test_active_index_portion_prevents_delete_without_any_changes(services, pg_engine):
    knowledge, indexes, _, artifacts, actor = setup(services, pg_engine)
    ref = register(artifacts, actor)
    with indexes.claim() as job:
        assert job['artifact_id'] == ref.artifact_id
        before = {table:rows(pg_engine,table) for table in ('artifacts','knowledge_jobs')}
        with pytest.raises(CoreError, match='ARTIFACT_BUSY'):
            artifacts.delete(actor, ref.artifact_id)
        assert {table:rows(pg_engine,table) for table in before} == before
        assert artifacts.read(actor, ref).content
    artifacts.delete(actor, ref.artifact_id)


def test_failed_cleanup_does_not_starve_other_deleted_files(services, pg_engine, monkeypatch):
    artifacts, _, store, actor = services
    refs = sorted([register(artifacts,actor,'one'),register(artifacts,actor,'two')],key=lambda r:r.artifact_id)
    metadata = [artifacts.metadata(actor,r) for r in refs]
    original_delete = store.delete
    def unavailable(_):
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR','Cannot unlink')
    monkeypatch.setattr(store,'delete',unavailable)
    for ref in refs:
        with pytest.raises(CoreError,match='ARTIFACT_CLEANUP_PENDING'):
            artifacts.delete(actor,ref.artifact_id)
    def partial(object_ref):
        if object_ref == metadata[0]['object_ref']: unavailable(object_ref)
        original_delete(object_ref)
    monkeypatch.setattr(store,'delete',partial)
    with pytest.raises(CoreError,match='ARTIFACT_CLEANUP_PENDING'):
        artifacts.cleanup_deleted()
    digest=metadata[1]['sha256']
    assert not (store.root/digest[:2]/digest).exists()
    with pg_engine.connect() as c:
        assert c.execute(text('SELECT artifact_id FROM artifact_object_deletions')).scalars().all()==[refs[0].artifact_id]


def test_legacy_shared_dataset_keeps_other_files_rows_and_valid_current_revision(services,pg_engine):
    artifacts, _, _, actor=services
    first=register(artifacts,actor,'remove this')
    other=register(artifacts,actor,'keep this')
    dataset,snapshot,run=[uuid4() for _ in range(3)]
    with pg_engine.begin() as c:
        c.execute(text("INSERT INTO schema_definitions VALUES('legacy','1',:hash,'{}')"),{'hash':'0'*64})
        c.execute(text("INSERT INTO input_snapshots VALUES(:id,:scope,'[]')"),dict(id=snapshot,scope=actor.data_scope))
        c.execute(text("INSERT INTO process_runs VALUES(:id,:scope,:snapshot,'{}',1,'completed',now())"),dict(id=run,scope=actor.data_scope,snapshot=snapshot))
        c.execute(text("INSERT INTO datasets VALUES(:id,:artifact,:scope,'legacy',2)"),dict(id=dataset,artifact=first.artifact_id,scope=actor.data_scope))
        for revision,ref in ((1,other),(2,first)):
            c.execute(text("""INSERT INTO dataset_revisions VALUES(:id,:rev,:artifact,:snapshot,:run,'legacy','1','{}',1)"""),
                      dict(id=dataset,rev=revision,artifact=ref.artifact_id,snapshot=snapshot,run=run))
            c.execute(text("INSERT INTO data_records VALUES(:id,:rev,1,:record,CAST(:value AS jsonb))"),dict(id=dataset,rev=revision,record=str(ref.artifact_id),value=json.dumps({'value':str(ref.artifact_id)})))
    preserved={table:rows(pg_engine,table) for table in ('input_snapshots','process_runs','schema_definitions')}
    artifacts.delete(actor,first.artifact_id)
    assert artifacts.read(actor,other).content==b'keep this'
    with pg_engine.connect() as c:
        assert c.execute(text('SELECT revision FROM dataset_revisions')).scalars().all()==[1]
        assert c.execute(text('SELECT record_id FROM data_records')).scalars().all()==[str(other.artifact_id)]
        assert c.execute(text('SELECT current_revision FROM datasets')).scalar()==1
    assert {table:rows(pg_engine,table) for table in preserved}==preserved


def test_read_racing_deletion_returns_gone_instead_of_not_found(services,monkeypatch):
    artifacts,repository,_,actor=services
    ref=register(artifacts,actor)
    check=repository._active_artifact
    deleted=False
    def concurrent_delete(connection,context,artifact_id):
        nonlocal deleted
        check(connection,context,artifact_id)
        if not deleted:
            deleted=True
            artifacts.delete(actor,ref.artifact_id)
    monkeypatch.setattr(repository,'_active_artifact',concurrent_delete)
    with pytest.raises(CoreError,match='ARTIFACT_DELETED'):
        artifacts.metadata(actor,ref)
