"""Real PostgreSQL persistence, vectors, scope, revisions and resumable portions."""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text

from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact


class Vectors:
    space='text-embedding-3-small:1536'
    dimensions=1536
    max_tokens=8192
    def __init__(self): self.fail=False; self.tokenized=0
    def count_tokens(self,value): self.tokenized+=1; return len(value.encode())
    def embed(self,texts,**kwargs):
        if self.fail: raise CoreError('EMBEDDING_RATE_LIMIT','Provider limit')
        return [[1.0, *([0.0]*1535)] if 'Болт' in value else [0.0,1.0,*([0.0]*1534)] for value in texts]


def setup(services,pg_engine):
    from bervel.core.knowledge import KnowledgeService
    from bervel.core.infrastructure.knowledge import PostgresKnowledgeRepository
    from bervel.core.infrastructure.document_reader import DocumentReader
    artifacts,_,_,actor=services
    repo=PostgresKnowledgeRepository(pg_engine)
    model=Vectors()
    return KnowledgeService(repo,artifacts,DocumentReader(),model),repo,model,artifacts,actor


def upload(artifacts,actor,text_value='Болт М16',**kwargs):
    return artifacts.register(actor,RegisterArtifact(text_value.encode(),'profile.txt','text/plain',{},uuid4().hex,**kwargs))


def test_upload_queues_exact_revision_atomically_and_does_not_duplicate(services,pg_engine):
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    command=RegisterArtifact('Болт'.encode(),'profile.txt','text/plain',{},'same-upload')
    ref=artifacts.register(actor,command)
    assert artifacts.register(actor,command)==ref
    assert repo.status(actor,ref)['state']=='pending'
    assert service.run_once()
    status=repo.status(actor,ref)
    assert status['state']=='ready' and status['completed_chunks']==status['total_chunks']>0
    with pg_engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM knowledge_jobs')).scalar()==1
        assert c.execute(text("SELECT public.vector_dims(embedding) FROM knowledge_chunks")).scalar()==1536


def test_unknown_format_has_explicit_unsupported_state(services,pg_engine):
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    ref=artifacts.register(actor,RegisterArtifact(b'\x00\xff','drawing.custom','application/octet-stream',{},uuid4().hex))
    assert service.run_once()
    assert repo.status(actor,ref)['state']=='unsupported'


def test_failed_portion_keeps_completed_vectors_and_requires_explicit_resume(services,pg_engine):
    service,repo,model,artifacts,actor=setup(services,pg_engine)
    content='\n\n'.join('Болт '+str(i)+' '+('проверить покрытие '*70) for i in range(28))
    ref=upload(artifacts,actor,content)
    assert service.run_once()
    first=repo.status(actor,ref)
    assert 0<first['completed_chunks']<first['total_chunks']
    model.fail=True
    assert service.run_once() and repo.status(actor,ref)['state']=='failed'
    assert not service.run_once()
    repo.resume(actor,ref)
    model.fail=False
    while service.run_once(): pass
    assert repo.status(actor,ref)['state']=='ready'
    with pg_engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM knowledge_chunks WHERE embedding IS NOT NULL')).scalar()==first['total_chunks']


def test_vector_search_cannot_return_other_scope_or_revision(services,pg_engine):
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    old=upload(artifacts,actor,'Болт OLD')
    new=upload(artifacts,actor,'Болт NEW')
    foreign=upload(artifacts,replace(actor,data_scope='other'),'Болт FOREIGN')
    while service.run_once(): pass
    sources=repo.search(actor,[artifacts.metadata(actor,new)],'Болт',[1.0,*([0.0]*1535)])
    assert sources and all(s['artifact_id']==str(new.artifact_id) for s in sources)
    with pytest.raises(CoreError): repo.status(actor,foreign)


def test_archived_originals_leave_active_catalog_without_erasing_history(services,pg_engine):
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    ref=upload(artifacts,actor)
    assert service.run_once()
    catalog=[artifacts.metadata(actor,ref)]
    artifacts.delete(actor,ref.artifact_id)
    assert artifacts.list(actor)['artifacts']==[]
    with pytest.raises(CoreError, match='ARTIFACT_DELETED'):
        artifacts.read(actor,ref)
    assert repo.search(actor,catalog,'Болт',[1.0,*([0.0]*1535)])==[]


def test_job_enqueue_rolls_back_with_original_registration(services,pg_engine,monkeypatch):
    _,_,_,artifacts,actor=setup(services,pg_engine)
    def fail(*args,**kwargs): raise RuntimeError('before receipt commit')
    monkeypatch.setattr(artifacts.repository.receipts,'finish',fail)
    with pytest.raises(RuntimeError): upload(artifacts,actor)
    with pg_engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM artifacts')).scalar()==0
        assert c.execute(text('SELECT count(*) FROM knowledge_jobs')).scalar()==0


def test_next_portion_does_not_tokenize_entire_large_document_again(services,pg_engine):
    service,repo,model,artifacts,actor=setup(services,pg_engine)
    ref=upload(artifacts,actor,'\n\n'.join('Болт '+('проверка '*120) for _ in range(40)))
    assert service.run_once()
    first=model.tokenized
    assert first>0 and repo.status(actor,ref)['completed_chunks']<repo.status(actor,ref)['total_chunks']
    assert service.run_once()
    assert model.tokenized==first


def test_identical_content_under_another_filename_reuses_vectors_when_provider_down(services,pg_engine):
    service,repo,model,artifacts,actor=setup(services,pg_engine)
    first=upload(artifacts,actor,'Болт М16')
    while service.run_once(): pass
    second=artifacts.register(actor,RegisterArtifact('Болт М16'.encode(),'another-name.txt','text/plain',{},uuid4().hex))
    model.fail=True
    while service.run_once(): pass
    assert repo.status(actor,second)['state']=='ready'
    result=repo.search(actor,[artifacts.metadata(actor,second)],'Болт',[1.0,*([0.0]*1535)])
    assert result and result[0]['artifact_id']==str(second.artifact_id)
    assert all(r['artifact_id']!=str(first.artifact_id) for r in result)
