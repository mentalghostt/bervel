"""Existing PostgreSQL evidence remains usable when query embeddings are unavailable.

Only the external embedding provider is a fake. Originals, extraction, knowledge
jobs/chunks/vectors, scope checks and DocumentMemory all use their real adapters.
"""
from dataclasses import replace
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from bervel.core.document_memory import DocumentMemory
from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact
from test_knowledge import Vectors, setup


PROVIDER_FAILURES = (
    'EMBEDDING_REGION_UNAVAILABLE',
    'EMBEDDING_UNAVAILABLE',
    'EMBEDDING_AUTH_ERROR',
    'EMBEDDING_RATE_LIMIT',
    'EMBEDDING_UNCONFIGURED',
    'EMBEDDING_INVALID_RESPONSE',
)
UPSTREAM_DETAIL = 'private-provider-response-must-not-be-returned'


class UnavailableEmbeddings(Vectors):
    """Index first, then refuse any further external embedding operation."""
    error = None

    def embed(self, texts, **kwargs):
        if self.error is not None:
            raise self.error
        return super().embed(texts, **kwargs)


def real_memory(services, pg_engine):
    knowledge, repository, _, artifacts, actor = setup(services, pg_engine)
    provider = UnavailableEmbeddings()
    knowledge.embeddings = provider
    memory = DocumentMemory(artifacts, repository, knowledge.reader, knowledge=knowledge)
    return memory, knowledge, repository, provider, artifacts, actor


def register(artifacts, actor, content, filename='fasteners.txt', media_type='text/plain'):
    return artifacts.register(actor, RegisterArtifact(content.encode(), filename, media_type, {}, uuid4().hex))


def ready(knowledge, repository, actor, *references):
    while knowledge.run_once():
        pass
    assert all(repository.status(actor, reference)['state'] == 'ready' for reference in references)


def assert_lexical(source):
    assert source.get('retrieval_mode') == 'lexical', 'Fallback evidence must identify lexical retrieval'
    assert source.get('retrieval') not in ('hybrid', 'semantic'), 'Lexical evidence must not claim semantic search'
    assert isinstance(source.get('notice'), str) and source['notice'].strip(), 'Reduced search coverage must be explicit'
    assert UPSTREAM_DETAIL not in json.dumps(source, ensure_ascii=False), 'Provider internals must remain private'


@pytest.mark.parametrize('failure_code', PROVIDER_FAILURES)
def test_ready_real_index_still_supplies_verified_evidence_after_query_embedding_failure(services, pg_engine, failure_code):
    # Break caught: an external query failure is allowed to abort access to already indexed text.
    memory, knowledge, repository, provider, artifacts, actor = real_memory(services, pg_engine)
    wanted = register(artifacts, actor, 'Болт М16 ГОСТ 7798: класс прочности 8.8.')
    register(artifacts, actor, 'Логистика и доставка груза.', 'logistics.txt')
    ready(knowledge, repository, actor, wanted)
    catalog = artifacts.list(actor)['artifacts']
    provider.error = CoreError(failure_code, UPSTREAM_DETAIL)

    sources = memory.context(actor, catalog, 'Болт')

    assert sources, 'A provider outage must not hide already indexed relevant evidence'
    assert {source['artifact_id'] for source in sources} == {str(wanted.artifact_id)}
    assert any('Болт М16 ГОСТ 7798' in source['excerpt'] for source in sources)
    assert all(source['status'] == 'ready' for source in sources)
    for source in sources:
        assert_lexical(source)
    # Reading must neither request reindexing nor erase the completed vector index.
    status = repository.status(actor, wanted)
    assert status['state'] == 'ready' and status['completed_chunks'] == status['total_chunks'] > 0


def test_discovery_preserves_fallback_provenance_and_rejects_generic_word_only_controls(services, pg_engine):
    # Break caught: AND across a conversational question finds nothing; broad OR discloses unrelated candidates.
    memory, knowledge, repository, provider, artifacts, actor = real_memory(services, pg_engine)
    wanted = register(artifacts, actor, 'БЕРВЕЛ: болт М16 ГОСТ 7798, класс прочности 8.8. production крепежа.')
    for number in range(4):
        register(artifacts, actor, f'Production отчёт {number}: план доставки и расходы отдела.', f'delivery-{number}.txt')
    ready(knowledge, repository, actor, wanted)
    provider.error = CoreError('EMBEDDING_REGION_UNAVAILABLE', UPSTREAM_DETAIL)

    result = memory.search(actor,
        'Для ответа клиенту найди в нашей базе сведения о Болт М16 ГОСТ 7798. '
        'Меня интересуют характеристики этого крепежа и production, а затем подготовь краткую справку.')

    assert result['retrieval_complete'] is False
    assert result.get('retrieval_mode') == 'lexical'
    assert result.get('notice') and UPSTREAM_DETAIL not in result['notice']
    assert [item['artifact_id'] for item in result['matches']] == [str(wanted.artifact_id)]
    assert result['sources'] and all(source['artifact_id'] == str(wanted.artifact_id) for source in result['sources'])
    assert any('ГОСТ 7798' in source['excerpt'] for source in result['sources'])
    for source in result['sources']:
        assert_lexical(source)


def test_lexical_no_match_is_empty_with_explicit_incomplete_coverage(services, pg_engine):
    # Break caught: semantic-style top-k or catch-all success invents a match without lexical evidence.
    memory, knowledge, repository, provider, artifacts, actor = real_memory(services, pg_engine)
    reference = register(artifacts, actor, 'Болт М16 ГОСТ 7798.')
    ready(knowledge, repository, actor, reference)
    provider.error = CoreError('EMBEDDING_UNAVAILABLE', UPSTREAM_DETAIL)

    result = memory.search(actor, 'Криогенный теплообменник')

    assert result['matches'] == [] and result['sources'] == [] and result['shown'] == 0
    assert result['retrieval_complete'] is False and result.get('retrieval_mode') == 'lexical'
    assert result.get('notice') and UPSTREAM_DETAIL not in result['notice']


def test_lexical_no_match_does_not_present_unreadable_metadata_as_semantic_candidate(services, pg_engine):
    # Break caught: unavailable-file warnings become fabricated search matches when lexical hits are empty.
    memory, knowledge, repository, provider, artifacts, actor = real_memory(services, pg_engine)
    reference = register(artifacts, actor, 'Болт М16')
    artifacts.register(actor, RegisterArtifact(b'\x00\xff', 'unreadable.custom', 'application/octet-stream', {}, uuid4().hex))
    ready(knowledge, repository, actor, reference)
    provider.error = CoreError('EMBEDDING_UNAVAILABLE', UPSTREAM_DETAIL)

    result = memory.search(actor, 'Криогенный теплообменник')

    assert result['catalog_total'] == 2 and result['matches'] == [] and result['sources'] == []
    assert result.get('retrieval_mode') == 'lexical' and result['retrieval_complete'] is False


def test_fallback_discovery_uses_current_scope_and_does_not_reveal_deleted_sources(services, pg_engine):
    # Break caught: fallback skips normal catalog scope or reintroduces deleted cached chunks.
    memory, knowledge, repository, provider, artifacts, actor = real_memory(services, pg_engine)
    wanted = register(artifacts, actor, 'Болт М16 СВОЙ')
    foreign_actor = replace(actor, data_scope='foreign-resilience-area')
    register(artifacts, foreign_actor, 'Болт М16 ЧУЖОЙ', 'foreign-secret.txt')
    deleted = register(artifacts, actor, 'Болт М16 УДАЛЁННЫЙ', 'deleted-secret.txt')
    ready(knowledge, repository, actor, wanted, deleted)
    artifacts.delete(actor, deleted.artifact_id)
    provider.error = CoreError('EMBEDDING_AUTH_ERROR', UPSTREAM_DETAIL)

    result = memory.search(actor, 'Болт')

    assert result['catalog_total'] == 1
    assert [item['artifact_id'] for item in result['matches']] == [str(wanted.artifact_id)]
    encoded = json.dumps(result, ensure_ascii=False)
    assert 'ЧУЖОЙ' not in encoded and 'УДАЛЁННЫЙ' not in encoded
    assert 'foreign-secret.txt' not in encoded and 'deleted-secret.txt' not in encoded


def test_tampered_original_cannot_supply_cached_lexical_text(services, pg_engine):
    # Break caught: adding local search bypasses DocumentMemory's original integrity check.
    memory, knowledge, repository, provider, artifacts, actor = real_memory(services, pg_engine)
    reference = register(artifacts, actor, 'Болт М16 CONFIDENTIAL-CACHED-TEXT')
    ready(knowledge, repository, actor, reference)
    snapshot = artifacts.metadata(actor, reference)
    digest = snapshot['sha256']
    (services[2].root / digest[:2] / digest).write_bytes(b'tampered-original')
    provider.error = CoreError('EMBEDDING_RATE_LIMIT', UPSTREAM_DETAIL)

    sources = memory.context(actor, [snapshot], 'Болт')

    assert sources and all(source['status'] == 'metadata_only' for source in sources)
    assert all('CONFIDENTIAL-CACHED-TEXT' not in source['excerpt'] for source in sources)


@pytest.mark.parametrize('read_kind', ['complete', 'page'])
def test_known_file_full_and_paged_reads_require_no_query_embedding(services, pg_engine, read_kind):
    # Break caught: known-file reading acquires an unnecessary external query dependency.
    memory, knowledge, repository, provider, artifacts, actor = real_memory(services, pg_engine)
    reference = register(artifacts, actor,
        'product,quantity\nБолт М16,11\nГайка М16,22\nШайба М16,33\n', 'known.csv', 'text/csv')
    ready(knowledge, repository, actor, reference)
    provider.error = CoreError('EMBEDDING_REGION_UNAVAILABLE', UPSTREAM_DETAIL)

    if read_kind == 'complete':
        sources = memory.context(actor, [artifacts.metadata(actor, reference)], '', complete=True)
        assert len(sources) == 3 and all(not source['truncated'] for source in sources)
        assert any('Шайба М16' in source['excerpt'] for source in sources)
        assert all(source['retrieval'] == 'complete' for source in sources)
    else:
        page = memory.read_page(actor, reference, offset=2, limit=1)
        assert page['total'] == 3 and page['shown'] == 1 and page['next_offset'] is None
        assert page['source_complete'] is True and page['offset'] == 2
        assert 'Шайба М16' in page['sources'][0]['excerpt']
        assert page['sources'][0]['retrieval'] == 'page'


@pytest.mark.parametrize('failure_type', ['programming', 'unknown_core'])
def test_unknown_programming_failures_are_not_disguised_as_lexical_success(services, pg_engine, failure_type):
    # Break caught: except Exception/CoreError silently hides broken application logic.
    memory, knowledge, repository, provider, artifacts, actor = real_memory(services, pg_engine)
    reference = register(artifacts, actor, 'Болт М16')
    ready(knowledge, repository, actor, reference)
    provider.error = RuntimeError('programming failure') if failure_type == 'programming' else CoreError('INTERNAL_BUG', 'programming failure')

    with pytest.raises(RuntimeError if failure_type == 'programming' else CoreError) as caught:
        memory.context(actor, [artifacts.metadata(actor, reference)], 'Болт')

    if failure_type == 'unknown_core':
        assert caught.value.code == 'INTERNAL_BUG'


def test_service_model_space_guard_is_not_suppressed_during_provider_outage(services, pg_engine):
    # Break caught: fallback catches KNOWLEDGE_REINDEX_REQUIRED and bypasses the established space guard.
    memory, knowledge, repository, provider, artifacts, actor = real_memory(services, pg_engine)
    reference = register(artifacts, actor, 'Болт М16')
    ready(knowledge, repository, actor, reference)
    provider.space = 'incompatible-query-model:1536'
    provider.error = CoreError('EMBEDDING_UNAVAILABLE', UPSTREAM_DETAIL)

    with pytest.raises(CoreError) as caught:
        memory.context(actor, [artifacts.metadata(actor, reference)], 'Болт')

    assert caught.value.code == 'KNOWLEDGE_REINDEX_REQUIRED'


def test_repository_lexical_search_keeps_eligible_scope_state_deletion_and_model_guards(services, pg_engine):
    # Break caught: new SQL fallback drops any existing _eligible constraint.
    _, knowledge, repository, _, artifacts, actor = real_memory(services, pg_engine)
    wanted = register(artifacts, actor, 'Болт М16 СВОЙ', 'wanted.txt')
    wrong_model = register(artifacts, actor, 'Болт М16 ДРУГАЯ-МОДЕЛЬ', 'wrong-model.txt')
    failed = register(artifacts, actor, 'Болт М16 НЕГОТОВЫЙ', 'failed.txt')
    deleted = register(artifacts, actor, 'Болт М16 УДАЛЁННЫЙ', 'deleted.txt')
    foreign_actor = replace(actor, data_scope='foreign-lexical-area')
    foreign = register(artifacts, foreign_actor, 'Болт М16 ЧУЖОЙ', 'foreign.txt')
    ready(knowledge, repository, actor, wanted, wrong_model, failed, deleted)
    catalog = [artifacts.metadata(actor, reference) for reference in (wanted, wrong_model, failed, deleted)]
    catalog.append(artifacts.metadata(foreign_actor, foreign))
    # Controlled state in the dedicated random test schema; no application DB is touched.
    with pg_engine.begin() as connection:
        connection.execute(text('UPDATE knowledge_chunks SET model=:model WHERE artifact_id=:id'),
            {'model': 'incompatible-document-model:1536', 'id': wrong_model.artifact_id})
        connection.execute(text("UPDATE knowledge_jobs SET state='failed' WHERE artifact_id=:id"), {'id': failed.artifact_id})
        connection.execute(text('UPDATE artifacts SET deleted_at=now() WHERE artifact_id=:id'), {'id': deleted.artifact_id})

    lexical_search = getattr(repository, 'lexical_search', None)
    assert callable(lexical_search), 'Repository needs scoped lexical retrieval independent of a query vector'
    sources = lexical_search(actor, catalog, 'Болт')

    assert sources and {source['artifact_id'] for source in sources} == {str(wanted.artifact_id)}
    assert all('СВОЙ' in source['excerpt'] for source in sources)
