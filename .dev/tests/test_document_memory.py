from bervel.core.infrastructure.documents import PostgresDocumentRepository
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from bervel.core.document_memory import DocumentMemory
from bervel.modules.chat.service import ChatService
from bervel.core.models import RegisterArtifact
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from bervel.core.infrastructure.document_reader import DocumentReader


class Model:
    def __init__(self): self.calls = []
    def generate(self, request_id, prompt, **kwargs):
        self.calls.append(prompt)
        return {'answer': 'Ответ по источникам', 'model': 'test'}


@pytest.fixture
def documents(services, pg_engine):
    artifacts, _, _, actor = services
    actor = replace(actor, permissions=actor.permissions | {'chat:read', 'chat:write'})
    repo = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine))
    memory = DocumentMemory(artifacts, repo.documents, DocumentReader())
    model = Model()
    return ChatService(repo, memory, model), artifacts, actor, repo, model


def add(artifacts, actor, name, content, mime='text/plain', **kwargs):
    return artifacts.register(actor, RegisterArtifact(content, name, mime, {}, uuid4().hex, **kwargs))


def ask(service, actor, question):
    sid = UUID(service.create(actor, uuid4().hex)['session_id'])
    service.send(actor, sid, uuid4().hex, question)
    assert service.run_once()
    return service.read(actor, sid)['turns'][0]


def test_searches_old_files_and_combines_sources_from_multiple_documents(documents):
    service, artifacts, actor, _, model = documents
    first = add(artifacts, actor, 'alpha.csv', b'name,quantity\nUNIQUE_ALPHA,17\n', 'text/csv')
    second = add(artifacts, actor, 'beta.csv', b'name,quantity\nUNIQUE_BETA,83\n', 'text/csv')
    add(artifacts, actor, 'later.txt', b'UNRELATED')
    result = ask(service, actor, 'Сравни UNIQUE_ALPHA и UNIQUE_BETA из alpha.csv и beta.csv')
    assert result['state'] == 'completed'
    assert {s['artifact_id'] for s in result['sources']} >= {str(first.artifact_id), str(second.artifact_id)}
    assert 'UNIQUE_ALPHA' in model.calls[-1] and 'UNIQUE_BETA' in model.calls[-1]
    assert all('unit' in source and 'status' in source for source in result['sources'])


def test_unknown_binary_is_findable_by_name_but_not_claimed_read(documents):
    service, artifacts, actor, _, model = documents
    original = add(artifacts, actor, 'drawing-4826.custom', b'\x00\xff\x01opaque', 'application/octet-stream')
    result = ask(service, actor, 'Найди drawing-4826.custom')
    assert result['state'] == 'completed'
    source = next(s for s in result['sources'] if s['artifact_id'] == str(original.artifact_id))
    assert source['unit'] == 'metadata' and source['status'] == 'metadata_only'
    assert 'not' in model.calls[-1].lower()


def test_current_revisions_scope_and_enqueue_snapshot_are_preserved(documents):
    service, artifacts, actor, _, model = documents
    ref = add(artifacts, actor, 'contract.txt', b'OLD_410')
    current = add(artifacts, actor, 'contract.txt', b'NEW_910')
    add(artifacts, replace(actor, data_scope='foreign'), 'secret.txt', b'FOREIGN_SECRET_882')
    sid = UUID(service.create(actor, 's')['session_id'])
    service.send(actor, sid, 'm', 'Найди contract.txt')
    add(artifacts, actor, 'late.txt', b'LATE_112')
    assert service.run_once()
    result = service.read(actor, sid)['turns'][0]
    assert result['state'] == 'completed'
    assert any(s['artifact_id'] == str(current.artifact_id) for s in result['sources'])
    assert 'FOREIGN_SECRET_882' not in model.calls[-1] and 'LATE_112' not in model.calls[-1] and 'OLD_410' in model.calls[-1]


def test_explicit_latest_csv_still_ignores_newer_non_csv(documents):
    service, artifacts, actor, _, _ = documents
    add(artifacts, actor, 'old.csv', b'id,value\n1,OLD\n', 'text/csv')
    current = add(artifacts, actor, 'new.csv', b'id,value\n2,NEW\n', 'text/csv')
    add(artifacts, actor, 'newest.txt', b'not csv')
    result = ask(service, actor, 'Что в самом последнем csv?')
    assert {source['artifact_id'] for source in result['sources']} == {str(current.artifact_id)}


def test_shared_memory_does_not_interpret_chat_latest_csv_phrase(documents):
    service, artifacts, actor, repo, _ = documents
    first = add(artifacts, actor, 'old.csv', b'id,value\n1,OLD\n', 'text/csv')
    second = add(artifacts, actor, 'new.csv', b'id,value\n2,NEW\n', 'text/csv')
    with repo.engine.connect() as connection:
        snapshot = repo.documents.catalog(connection, actor.data_scope)
    sources = service.memory.context(actor, snapshot, 'Что в последнем csv?')
    assert {s['artifact_id'] for s in sources} == {str(first.artifact_id), str(second.artifact_id)}


def test_chat_latest_uses_enqueued_snapshot_but_compare_keeps_both(documents):
    service, artifacts, actor, _, _ = documents
    first = add(artifacts, actor, 'old.csv', b'id,value\n1,OLD\n', 'text/csv')
    second = add(artifacts, actor, 'current.csv', b'id,value\n2,CURRENT\n', 'text/csv')
    sid = UUID(service.create(actor, 'pin-chat')['session_id'])
    service.send(actor, sid, 'pin-message', 'Что в последнем CSV?')
    add(artifacts, actor, 'late.csv', b'id,value\n3,LATE\n', 'text/csv')
    assert service.run_once()
    assert {s['artifact_id'] for s in service.read(actor, sid)['turns'][0]['sources']} == {str(second.artifact_id)}
    compared = ask(service, actor, 'Сравни оба CSV, включая последний CSV')
    assert {str(first.artifact_id), str(second.artifact_id)} <= {s['artifact_id'] for s in compared['sources']}


def test_large_matching_file_cannot_hide_a_second_named_file(documents):
    service, artifacts, actor, _, _ = documents
    beta = add(artifacts, actor, 'beta.csv', b'name,quantity\nBETA_KEY,83\n', 'text/csv')
    alpha = add(artifacts, actor, 'alpha.csv', ('name,value\n' + ''.join(f'ALPHA_KEY,{"x" * 250}\n' for _ in range(250))).encode(), 'text/csv')
    result = ask(service, actor, 'Сравни ALPHA_KEY в alpha.csv с beta.csv')
    assert {s['artifact_id'] for s in result['sources']} >= {str(alpha.artifact_id), str(beta.artifact_id)}
    assert sum(len(s['excerpt']) for s in result['sources']) <= 16000


def test_bad_unrelated_original_does_not_block_a_healthy_named_file(documents, services):
    service, artifacts, actor, _, model = documents
    bad = add(artifacts, actor, 'broken.txt', b'BAD_ORIGINAL_924')
    metadata = artifacts.metadata(actor, bad)
    store = services[2]
    digest = metadata['object_ref'].split(':')[1]
    (store.root / digest[:2] / digest).write_bytes(b'tampered')
    good = add(artifacts, actor, 'healthy.txt', b'HEALTHY_VALUE_817')
    result = ask(service, actor, 'Найди healthy.txt и значение HEALTHY_VALUE_817')
    assert result['state'] == 'completed'
    assert any(s['artifact_id'] == str(good.artifact_id) for s in result['sources'])
    assert 'BAD_ORIGINAL_924' not in model.calls[-1]


def test_cached_content_is_never_used_when_original_becomes_unreadable(documents, services):
    service, artifacts, actor, _, model = documents
    bad = add(artifacts, actor, 'cached.txt', b'CACHED_PRIVATE_VALUE_1982')
    assert ask(service, actor, 'Найди cached.txt')['state'] == 'completed'
    metadata = artifacts.metadata(actor, bad)
    digest = metadata['object_ref'].split(':')[1]
    (services[2].root / digest[:2] / digest).write_bytes(b'tampered')
    result = ask(service, actor, 'Найди cached.txt')
    assert result['state'] == 'completed'
    assert all(source['unit'] == 'metadata' and source['status'] == 'metadata_only' for source in result['sources'])
    assert 'CACHED_PRIVATE_VALUE_1982' not in model.calls[-1].split('CURRENT_DOCUMENTS', 1)[-1].split('RECENT_CONVERSATION', 1)[0]


@pytest.mark.parametrize('old_version', [2, 3])
def test_previous_decoder_index_is_rebuilt_from_original(documents, pg_engine, old_version):
    from sqlalchemy import insert, select
    from bervel.core.infrastructure.database import document_indexes
    service, artifacts, actor, _, model = documents
    ref = add(artifacts, actor, 'legacy.csv', 'Код;Имя\n0016;Болт\n'.encode('cp1251'), 'text/csv')
    with pg_engine.begin() as connection:
        connection.execute(insert(document_indexes).values(artifact_id=ref.artifact_id, version=old_version,
            result={'status': 'ready', 'chunks': [{'row_start': 1, 'row_end': 1, 'total_rows': 1, 'unit': 'row', 'excerpt': 'OLD_DECODER_CONTENT'}]}))
    result = ask(service, actor, 'Что содержит legacy.csv?')
    assert result['state'] == 'completed'
    assert 'Болт' in model.calls[-1] and 'OLD_DECODER_CONTENT' not in model.calls[-1]
    with pg_engine.connect() as connection:
        assert connection.execute(select(document_indexes.c.version)).scalar_one() == 5
