from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from threading import Thread

import pytest

from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender


@pytest.fixture
def request_simulator():
    from kontur_simulator import create_server

    with create_server(port=0, max_requests_per_second=0) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()

        def request(method, path, body=None, *, key='bervel-kontur-simulator'):
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                connection.request(method, '/external/v1/' + path,
                    None if body is None else json.dumps(body),
                    {'X-Kontur-Apikey': key, 'Content-Type': 'application/json'})
                response = connection.getresponse()
                return response.status, json.load(response)
            finally:
                connection.close()

        try:
            request.url = f'http://127.0.0.1:{server.server_port}'
            yield request
        finally:
            server.shutdown()
            thread.join()


def search_body(text='закупка', page=0):
    end = datetime.now(timezone(timedelta(hours=3)))
    return {'Text': [text], 'PurchaseStatuses': [1], 'PageNumber': page,
            'DateTimeFrom': (end - timedelta(days=365)).isoformat(), 'DateTimeTo': end.isoformat(),
            'Attachments': True, 'MaxPriceNone': True, 'AllowForeignCurrency': True}


def test_search_pages_use_stable_ids_that_resolve_to_full_cards(request_simulator):
    status, first = request_simulator('POST', 'search', search_body())
    assert status == 200
    assert first['TotalCount'] > 10_000
    assert first['PageNumber'] == 0
    assert len(first['Items']) == 50

    for page in (1, 199):
        status, result = request_simulator('POST', 'search', search_body(page=page))
        assert status == 200
        assert result['PageNumber'] == page
        assert len(result['Items']) == 50
        assert not {item['Id'] for item in result['Items']} & {item['Id'] for item in first['Items']}

    item = first['Items'][0]
    assert 'Lots' not in item
    assert datetime.fromisoformat(item['CreateDate']) < datetime.fromisoformat(item['ApplicationDeadline'])
    status, card = request_simulator('GET', f"purchases/{item['Id']}")
    assert status == 200
    assert card['InitialSum'] == {'Price': item['MaxPrice'], 'Currency': item['Currency']}
    tender = normalize_tender(item, card)
    assert tender['title'] == item['OrderName']
    assert tender['positions'][0]['quantity'] == '120'
    assert tender['positions'][0]['unit'] == 'шт'
    assert tender['limitations'] == []


def test_search_honors_words_dates_and_attachment_switch(request_simulator):
    for text, overrides in [
        ('нет такого товара', {}),
        ('крепёж', {'DateTimeFrom': '2000-01-01T00:00:00Z', 'DateTimeTo': '2000-01-02T00:00:00Z'}),
        ('метизный-резерв', {'Attachments': False}),
    ]:
        status, result = request_simulator('POST', 'search', search_body(text) | overrides)
        assert status == 200
        assert result == {'PageNumber': 0, 'TotalCount': 0, 'Items': []}

    status, result = request_simulator('POST', 'search', search_body('метизный-резерв'))
    assert status == 200 and result['Items']
    status, result = request_simulator('POST', 'search', search_body('БОЛТ М12') | {'Attachments': False})
    assert status == 200
    assert 0 < result['TotalCount'] < 10_000
    assert all('болт м12' in item['OrderName'].casefold() for item in result['Items'])


def test_unlimited_simulator_has_no_search_or_purchase_quota():
    from kontur_simulator import create_server

    with create_server(port=0, profile='selection', max_requests_per_second=0, unlimited_requests=True) as server:
        assert server.limit_groups() == []
        for group in ('search', 'purchase'):
            assert all(server.admit(group) == 200 for _ in range(205))
        assert server.limit_groups() == []


@pytest.mark.parametrize('first_group', ['search', 'purchase'])
def test_each_quota_allows_200_requests_and_rejects_the_next(request_simulator, first_group):
    calls = {'search': ('POST', 'search', search_body('нет такого товара')),
             'purchase': ('GET', 'purchases/SIM-00001')}
    assert request_simulator('GET', 'limitGroups', key='wrong-key')[0] == 401

    def remaining():
        status, groups = request_simulator('GET', 'limitGroups')
        assert status == 200
        return {group['LimitGroup']: group['AvailableRequests'] for group in groups}

    second_group = 'purchase' if first_group == 'search' else 'search'
    assert remaining() == {'search': 200, 'purchase': 200}
    for group in (first_group, second_group):
        for _ in range(200):
            assert request_simulator(*calls[group])[0] == 200
        assert request_simulator(*calls[group])[0] == 429
        assert remaining() == {first_group: 0, second_group: 200 if group == first_group else 0}


def test_corpus_contains_mostly_unrelated_purchases_and_no_title_prefilter(request_simulator):
    status, result = request_simulator('POST', 'search', search_body('закупка') | {
        'PurchaseStatuses': [], 'DateTimeFrom': '2020-01-01T00:00:00Z'})
    assert status == 200 and result['TotalCount'] >= 100_000
    unrelated = [item for item in result['Items'] if 'ГОСТ' not in item['OrderName']]
    assert len(unrelated) >= 45
    status, found = request_simulator('POST', 'search', search_body('болт') | {'Attachments': True})
    assert status == 200
    service = next(item for item in found['Items'] if 'обслуживан' in item['OrderName'])
    status, card = request_simulator('GET', 'purchases/' + service['Id'])
    assert status == 200
    assert 'Болт' not in card['Lots'][0]['JointObjects'][0]['Name']


def test_price_status_exclusion_and_page_ceiling_are_applied(request_simulator):
    status, result = request_simulator('POST', 'search', search_body() | {'PurchaseStatuses': [5]})
    assert status == 200 and result['Items']
    assert all(item['IsCancelled'] for item in result['Items'])
    status, result = request_simulator('POST', 'search', search_body('болт, гайка') | {
        'Exclude': ['обслуживание'], 'MaxPriceFrom': 1, 'MaxPriceTo': 100_000,
        'MaxPriceNone': False, 'AllowForeignCurrency': False})
    assert status == 200 and result['Items']
    assert all(1 <= item['MaxPrice'] <= 100_000 and item['Currency'] == 'RUB' for item in result['Items'])
    assert request_simulator('POST', 'search', search_body(page=200))[0] == 400
    assert request_simulator('GET', 'purchases/does-not-exist')[0] == 404


def test_same_key_has_a_shared_five_requests_per_second_limit():
    from kontur_simulator import create_server

    with create_server(port=0) as server:
        assert [server.admit('search', now=0.0) for _ in range(5)] == [200] * 5
        assert server.admit('purchase', now=0.5) == 429
        assert server.admit('purchase', now=1.01) == 200


def test_full_cards_include_mixed_missing_masked_and_multiple_lot_cases(request_simulator):
    for key, count, limitations in [
        ('SIM-00121', 2, []),
        ('SIM-00141', 0, ['LOT_OBJECTS_MISSING']),
        ('SIM-00161', 1, ['POSITION_REQUIREMENTS_INCOMPLETE']),
        ('SIM-00641', 2, []),
    ]:
        status, card = request_simulator('GET', f'purchases/{key}')
        assert status == 200
        tender = normalize_tender({'Id': key}, card)
        assert len(tender['positions']) == count
        assert [issue['code'] for issue in tender['limitations']] == limitations


def test_search_uses_whole_words_and_supports_explicit_prefixes():
    from kontur_simulator import matches

    assert matches('болт', 'Поставка болтов', False)
    assert matches('болт*', 'Поставка болтов', True)
    assert not matches('болт', 'Поставка болтов', True)
    assert not matches('м12', 'Поставка болтов М120', False)
    assert not matches('вирусный', 'Антивирусный продукт', False)


def test_region_law_category_customer_and_deadline_filters_work_together(request_simulator):
    now = datetime.now(timezone.utc)
    filters = {'RegionIds': [78], 'Laws': [2], 'CategoryIds': [2], 'IncludeInns': ['7700000000'],
               'ApplicationDeadlineFrom': (now + timedelta(days=7)).isoformat(),
               'ApplicationDeadlineTo': (now + timedelta(days=30)).isoformat()}
    status, result = request_simulator('POST', 'search', search_body() | filters)
    assert status == 200 and result['Items'][0]['Id'] == 'SIM-00022'
    assert all('обслуживание' in item['OrderName'] for item in result['Items'])
    status, result = request_simulator('POST', 'search', search_body() | filters | {'ExcludeInns': ['7700000000']})
    assert status == 200 and result['TotalCount'] == 0
