import json

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar import selection
from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient
from test_ai_radar_selection_http import TEST_KEY, search, selection_server


def test_model_covers_enabled_groups_and_normalizes_returned_phrases():
    class Model:
        def generate(self, request_id, prompt, **options):
            payload = json.loads(prompt.split('\nВХОД\n', 1)[1])
            assert [g['name'] for g in payload['groups']] == ['Болты', 'Трубы']
            assert '7798-70' in prompt and '7805-70' in prompt
            assert 'Инструмент' not in prompt and 'Гайки' not in prompt
            assert options['web_search'] is False
            return {'answer': json.dumps({'groups': [
                {'id': payload['groups'][0]['id'], 'keywords': ['Болт', ' болт ', 'Болт ГОСТ 7798-70']},
                {'id': payload['groups'][1]['id'], 'keywords': ['Труба']},
            ]}), 'model': 'fixture'}

    def product(name, subtype, standard, *, enabled=True):
        return {'enabled': enabled, 'name': name,
                'attributes': {'product_subtype': subtype, 'standard': standard}}

    profile = {'products': [product('Болт М12', 'Болты', 'ГОСТ 7798-70'),
                            product('Болт М14', 'Болты', 'ГОСТ 7805-70'),
                            product('Труба 23', 'Трубы', 'ГОСТ 8732-78'),
                            product('Инструмент', 'Инструмент', 'СТО 123', enabled=False)],
               'experimental_products': [product('Гайка М8', 'Гайки', 'ГОСТ 5915-70')],
               'include_experimental': False}
    assert selection.model_search_queries(profile, Model(), 'test', heartbeat=lambda: None) == [
        'Болт', 'Болт ГОСТ 7798-70', 'Труба']


@pytest.mark.parametrize('groups', [[], [{'id': 'unknown', 'keywords': ['болт']}],
                                   [{'id': '1', 'keywords': []}]])
def test_model_cannot_omit_groups_or_submit_empty_search(groups):
    class Model:
        def generate(self, request_id, prompt, **options):
            return {'answer': json.dumps({'groups': groups}), 'model': 'fixture'}

    with pytest.raises(CoreError) as error:
        selection.model_search_queries({'products': [{'enabled': True, 'name': 'Болт'}]},
                                       Model(), 'test', heartbeat=lambda: None)
    assert error.value.code == 'MODEL_INVALID_OUTPUT'


def test_combined_search_sends_exact_array_and_reuses_it_on_resume(monkeypatch):
    requests = []
    original = KonturClient._request

    def tracked(self, path, body=None, **options):
        requests.append((path, body))
        return original(self, path, body, **options)

    monkeypatch.setattr(KonturClient, '_request', tracked)
    keywords = ['закупка', 'болт']
    with selection_server() as (server, _):
        source = KonturClient(TEST_KEY, base_url=f'http://127.0.0.1:{server.server_port}', min_interval=0)
        initial = {'search_start': search()['DateTimeFrom'], 'search_end': search()['DateTimeTo']}
        archived = []

        def archive(page):
            archived.append(page)
            return str(len(archived))

        first = selection.collect_candidates(source, keywords, combined=True, search_max=1,
            checkpoint=initial, archive_page=archive, progress=lambda _: None, purchase_statuses=[1])
        second = selection.collect_candidates(source, ['замена'], combined=True, search_max=2,
            checkpoint=first, archive_page=archive, progress=lambda _: None, purchase_statuses=[1])
        assert server.remaining['purchase'] == 200

    assert [path for path, _ in requests] == ['/external/v1/search'] * 2
    assert [body['Text'] for _, body in requests] == [keywords, keywords]
    assert [body['PageNumber'] for _, body in requests] == [0, 1]
    assert all(body['PurchaseStatuses'] == [1] for _, body in requests)
    assert all(page['search_keywords'] == keywords for page in archived)
    assert second['search_keywords'] == keywords
    assert second['search_attempts'] == 2
    assert len({row['id'] for row in second['candidates']}) == len(second['candidates']) == 100
