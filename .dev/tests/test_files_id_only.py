from dataclasses import asdict, replace
import pytest
from bervel.core.models import RegisterArtifact
from bervel.core.errors import CoreError

def test_independent_files_retry_scope_and_shared_object_deletion(services):
    files, repo, store, actor = services
    command = RegisterArtifact(b'original', 'same.csv', 'text/csv', {}, 'first')
    first = files.register(actor, command)
    assert set(asdict(first)) == {'artifact_id'}
    second = files.register(actor, replace(command, command_key='second'))
    assert second.artifact_id != first.artifact_id
    assert files.register(actor, command) == first
    with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
        files.register(actor, replace(command, content=b'changed'))
    with pytest.raises(CoreError, match='NOT_FOUND'):
        files.read(replace(actor, data_scope='foreign'), first)
    files.delete(actor, first.artifact_id)
    original = files.read(actor, second)
    assert original.content == b'original'
    with pytest.raises(CoreError, match='ARTIFACT_DELETED'):
        files.register(actor, command)
    files.delete(actor, second.artifact_id)
    with pytest.raises(CoreError, match='TRANSIENT_DEPENDENCY_ERROR'):
        store.read(original.metadata['object_ref'])

def test_obsolete_revision_query_never_silently_selects_current_file(client):
    from test_api import PREFIX, auth, upload
    http, keys, _ = client
    ref = upload(http, keys[0]).json()
    for suffix in ('', '/content', '/preview', '/knowledge'):
        response = http.get(f"{PREFIX}/artifacts/{ref['artifact_id']}{suffix}?revision=1",headers=auth(keys[0]))
        assert response.status_code == 422

from test_api import client

def test_deleted_identity_has_sql_null_metadata(services, pg_engine):
    from sqlalchemy import text
    files, _, _, actor = services
    ref = files.register(actor, RegisterArtifact(b'one', 'one.txt', 'text/plain', {}, 'one'))
    files.delete(actor, ref.artifact_id)
    with pg_engine.connect() as connection:
        assert connection.execute(text('SELECT metadata IS NULL FROM artifacts WHERE artifact_id=:id'), {'id':ref.artifact_id}).scalar_one()

def test_fresh_file_schema_matches_runtime_metadata(pg_engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import inspect
    from bervel.core.infrastructure.database import metadata
    names={'artifacts','csv_chunks','document_indexes','dataset_revisions'}
    with pg_engine.connect() as connection:
        context=MigrationContext.configure(connection,opts={'include_name':lambda name,kind,parent_names:kind!='table' or name in names, 'include_object':lambda obj,name,kind,reflected,compare_to:kind!='table' or name in names})
        assert compare_metadata(context,metadata)==[]
        inspector=inspect(connection)
        for name in ('csv_chunks','document_indexes','knowledge_jobs','knowledge_chunks'):
            assert 'revision' not in {c['name'] for c in inspector.get_columns(name)}
            assert inspector.get_pk_constraint(name)['constrained_columns']==(['artifact_id','ordinal'] if name in ('csv_chunks','knowledge_chunks') else ['artifact_id'])

def test_browser_upload_database_verifier_uses_id_only_report(services, pg_engine, tmp_path):
    import json, runpy
    from pathlib import Path
    files, _, _, actor = services
    ref=files.register(actor,RegisterArtifact(b'browser upload','browser.txt','text/plain',{},'browser'))
    metadata=files.metadata(actor,ref)
    report=tmp_path/'report.json'
    report.write_text(json.dumps({k:metadata[k] for k in ('artifact_id','filename','sha256','size')}))
    helper=runpy.run_path(str(Path(__file__).parents[1]/'scripts'/'frontend_originals_verify.py'))
    assert helper['verify_database_results'](pg_engine,report)=={'originalStored':True,'bytesVerifiedByBrowser':True,'noEducationalDataset':True}

def test_delete_finishes_legacy_archive_with_retained_original_metadata(services, pg_engine):
    from sqlalchemy import text
    files, _, store, actor=services
    ref=files.register(actor,RegisterArtifact(b'legacy archive','archive.txt','text/plain',{},'archive'))
    original=files.metadata(actor,ref)
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE artifacts SET deleted_at='2026-10-01T00:00:00Z' WHERE artifact_id=:id"),{'id':ref.artifact_id})
    files.delete(actor,ref.artifact_id)
    with pg_engine.connect() as connection:
        row=connection.execute(text('SELECT metadata,deleted_at FROM artifacts WHERE artifact_id=:id'),{'id':ref.artifact_id}).mappings().one()
        assert row['metadata'] is None and row['deleted_at'].isoformat()=='2026-10-01T00:00:00+00:00'
    with pytest.raises(CoreError,match='TRANSIENT_DEPENDENCY_ERROR'):
        store.read(original['object_ref'])

@pytest.mark.parametrize('delete_current',[True,False])
def test_dataset_current_source_and_revision_stay_together_after_file_deletion(services, pg_engine, delete_current):
    from sqlalchemy import text
    from uuid import uuid4
    files, _, _, actor=services
    older=files.register(actor,RegisterArtifact(b'older source','older.txt','text/plain',{},'older'))
    current=files.register(actor,RegisterArtifact(b'current source','current.txt','text/plain',{},'current'))
    dataset,snapshot,run=uuid4(),uuid4(),uuid4()
    with pg_engine.begin() as connection:
        connection.execute(text("INSERT INTO schema_definitions VALUES('legacy','1',:hash,'{}')"),{'hash':'0'*64})
        connection.execute(text("INSERT INTO input_snapshots VALUES(:id,:scope,'[]')"),dict(id=snapshot,scope=actor.data_scope))
        connection.execute(text("INSERT INTO process_runs VALUES(:id,:scope,:snapshot,'{}',1,'completed',now())"),dict(id=run,scope=actor.data_scope,snapshot=snapshot))
        connection.execute(text("INSERT INTO datasets VALUES(:id,:artifact,:scope,'legacy',2)"),dict(id=dataset,artifact=current.artifact_id,scope=actor.data_scope))
        for number,ref in ((1,older),(2,current)):
            connection.execute(text("INSERT INTO dataset_revisions VALUES(:id,:number,:artifact,:snapshot,:run,'legacy','1','{}',1)"),dict(id=dataset,number=number,artifact=ref.artifact_id,snapshot=snapshot,run=run))
            connection.execute(text("INSERT INTO data_records VALUES(:id,:number,0,:record,'{}')"),dict(id=dataset,number=number,record=str(ref.artifact_id)))
    removed,surviving=(current,older) if delete_current else (older,current)
    files.delete(actor,removed.artifact_id)
    with pg_engine.connect() as connection:
        state=connection.execute(text('SELECT artifact_id,current_revision FROM datasets WHERE dataset_id=:id'),{'id':dataset}).mappings().one()
        assert state['artifact_id']==surviving.artifact_id
        assert state['current_revision']==(1 if delete_current else 2)
        assert connection.execute(text('SELECT artifact_id FROM dataset_revisions WHERE dataset_id=:id AND revision=:number'),{'id':dataset,'number':state['current_revision']}).scalar_one()==surviving.artifact_id
        assert connection.execute(text('SELECT record_id FROM data_records WHERE dataset_id=:id'),{'id':dataset}).scalars().all()==[str(surviving.artifact_id)]
    assert files.read(actor,surviving).content==(b'older source' if delete_current else b'current source')
