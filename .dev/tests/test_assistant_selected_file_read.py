"""Reading an explicitly selected original does not depend on search relevance."""
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact
from test_assistant_workflows import actor_with_chat, build_actions
from test_knowledge import Vectors
from test_knowledge_resilience import real_memory, ready


class RegionUnavailableQueries(Vectors):
    unavailable = False
    query_calls = 0

    def embed(self, texts, **kwargs):
        if self.unavailable:
            self.query_calls += 1
            if self.query_calls > 1:
                raise AssertionError('Selected-file reading must not retry query embeddings')
            raise CoreError('EMBEDDING_REGION_UNAVAILABLE', 'Embedding provider unavailable')
        return super().embed(texts, **kwargs)


@pytest.mark.parametrize('index_state', ['ready', 'pending', 'failed'])
def test_selected_ready_stock_is_read_in_order_when_general_question_has_no_lexical_hits(services, pg_engine, index_state):
    # Break caught: file.read incorrectly treats an empty query result as having read a selected file.
    memory, knowledge, repository, _, artifacts, _ = real_memory(services, pg_engine)
    actor = actor_with_chat(services)
    provider = RegionUnavailableQueries()
    knowledge.embeddings = provider
    content = ('sku,product,quantity,data_kind\nFAST-001,Болт М16,11,mock\n'
        + ''.join(f'FAST-{number:03},Гайка М{number},{number},mock\n' for number in range(2, 26)))
    reference = artifacts.register(actor,
        RegisterArtifact(content.encode(), 'current-stock.csv', 'text/csv', {}, uuid4().hex))
    artifacts.set_usage_role(actor, reference.artifact_id, 'stock')
    if index_state == 'ready':
        ready(knowledge, repository, actor, reference)
    provider.unavailable = True
    if index_state == 'failed':
        assert knowledge.run_once()
        assert knowledge.status(actor, reference)['state'] == 'failed'
    actions = build_actions(services, pg_engine)
    turn = uuid4()

    result = actions.execute(actor, turn, 0, 'file.read', {'artifact_id': str(reference.artifact_id)},
        memory=memory, question='Проанализируй наших конкурентов и подготовь сравнение рынка с указанием сайтов.')

    assert result['operation'] == 'file.read' and result['kind'] == 'data'
    data = result['data']
    sources = data['sources']
    assert sources, 'Reading a selected file must return actual content even when search has no matching words'
    assert data.get('read_mode') == 'page'
    assert data['file']['artifact_id'] == str(reference.artifact_id)
    assert data['file']['usage_role'] == 'stock'
    assert 0 < len(sources) < 25, 'A bounded first page must not claim to cover the whole file'
    assert all(source['artifact_id'] == str(reference.artifact_id) for source in sources)
    assert all(source['retrieval'] == 'page' and source['truncated'] is True for source in sources)
    assert [source['row_start'] for source in sources] == list(range(1, len(sources) + 1))
    assert all(source['unit'] == 'row' and source['row_end'] == source['row_start']
        and source['total_rows'] == 25 for source in sources)
    assert 'Болт М16' in sources[0]['excerpt'] and sources[0]['row_start'] == 1
    assert provider.query_calls <= 1, 'Known-file reading must not repeat the unavailable external query API'
    receipt = actions.receipts.read(actor, turn)[0]
    assert receipt['state'] == 'completed' and receipt['result']['data']['sources'] == sources
