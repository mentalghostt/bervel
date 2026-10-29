"""The actual shared memory consumes indexed text and verifies source originals."""
from dataclasses import replace
from uuid import uuid4

import pytest

from bervel.core.document_memory import DocumentMemory
from bervel.core.models import RegisterArtifact
from bervel.core.errors import CoreError
from test_knowledge import setup, upload


def test_chat_context_uses_real_vectors_and_cites_indexed_source(services,pg_engine):
    service,repo,model,artifacts,actor=setup(services,pg_engine)
    ref=upload(artifacts,actor)
    while service.run_once(): pass
    memory=DocumentMemory(artifacts,repo,service.reader,knowledge=service)
    sources=memory.context(actor,[artifacts.metadata(actor,ref)],'Болт')
    assert sources and 'Болт М16' in sources[0]['excerpt']
    assert sources[0]['artifact_id']==str(ref.artifact_id)
    assert sources[0]['retrieval']=='hybrid'


def test_full_profile_does_not_turn_into_top_k_search(services,pg_engine):
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    rows='product,standard\n'+''.join(f'Болт{i},ГОСТ{i}\n' for i in range(30))
    ref=artifacts.register(actor,RegisterArtifact(rows.encode(),'profile.csv','text/csv',{},uuid4().hex))
    while service.run_once(): pass
    memory=DocumentMemory(artifacts,repo,service.reader,knowledge=service)
    sources=memory.context(actor,[artifacts.metadata(actor,ref)],'',complete=True)
    assert len(sources)==30
    assert all(not s['truncated'] for s in sources)
    assert any('Болт29' in s['excerpt'] for s in sources)


def test_unindexed_file_is_not_silently_answered_from_stale_extraction(services,pg_engine):
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    ref=upload(artifacts,actor)
    memory=DocumentMemory(artifacts,repo,service.reader,knowledge=service)
    with pytest.raises(CoreError) as error: memory.context(actor,[artifacts.metadata(actor,ref)],'Болт',complete=True)
    assert error.value.code=='KNOWLEDGE_NOT_READY'


def test_tampered_original_cannot_supply_cached_semantic_evidence(services,pg_engine):
    service,repo,_,artifacts,actor=setup(services,pg_engine)
    ref=upload(artifacts,actor)
    while service.run_once(): pass
    m=artifacts.metadata(actor,ref); sha=m['sha256']
    (services[2].root/sha[:2]/sha).write_bytes(b'tampered')
    sources=DocumentMemory(artifacts,repo,service.reader,knowledge=service).context(actor,[m],'Болт')
    assert sources and all(s['status']=='metadata_only' for s in sources)
    assert all('Болт М16' not in s['excerpt'] for s in sources)
