from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
import json

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient, normalize_tender
from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock


def test_reads_entire_stock_and_preserves_cost_unknowns_and_evidence():
    header = 'sku,product,free_quantity,unit,unit_cost,currency,data_kind,standard\n'
    rows = ''.join(f'SKU-{i},Болт М12,{i},шт,,RUB,mock,ГОСТ 7798\n' for i in range(3076))
    stock = read_stock((header + rows).encode())
    assert stock['data_kind'] == 'mock'
    assert len(stock['rows']) == 3076
    assert stock['rows'][0]['free_quantity'] == '0'
    assert stock['rows'][-1] == {
        'row': 3077, 'sku': 'SKU-3075', 'product': 'Болт М12', 'free_quantity': '3075',
        'unit': 'шт', 'unit_cost': None, 'currency': 'RUB', 'as_of': None,
        'attributes': {'standard': 'ГОСТ 7798'},
    }


@pytest.mark.parametrize('rows', [
    'X,Bolt,2,шт\nX,Bolt,1,шт\n', 'X,Bolt,-2,шт\n', 'X,Bolt,1.5,шт\n',
    'X,Bolt,2,\n', 'X,Bolt,1,шт,unexpected\n',
    ''.join(f'X{i},Bolt,1,шт\n' for i in range(10001)),
], ids=['duplicate', 'negative', 'fractional-pieces', 'missing-unit', 'extra-cell', 'too-many-rows'])
def test_invalid_or_truncated_stock_cannot_be_used(rows):
    with pytest.raises(CoreError, match='(?i)склад') as error:
        read_stock(('sku,product,free_quantity,unit\n' + rows).encode())
    assert error.value.code == 'VALIDATION_ERROR'


def obj(quantity, *, price=None, value='М12'):
    return {'Id': 'duplicate-id', 'Name': 'Болт', 'Quantity': quantity, 'Okei': 'шт',
            'PositionPrice': price, 'Characteristics': [{'Name': 'Резьба',
                'Values': [{'Value': value, 'Okei': None}], 'FillingInstruction': 'Укажите значение'}]}


def test_joint_summary_is_not_a_second_demand_and_ids_are_occurrences():
    lot = {'Customers': [{'Objects': [obj(2, price=Decimal('2.01'))]}, {'Objects': [obj(3)]}],
           'JointObjects': [obj(5)]}
    tender = normalize_tender({'Id': 'СБ-1', 'Currency': 'руб.'},
                              {'Id': 'СБ-1', 'Title': 'Болты', 'Lots': [lot, lot]})
    positions = tender['positions']
    assert [row['quantity'] for row in positions] == ['2', '3', '2', '3']
    assert [row['id'] for row in positions] == ['1:1:1', '1:2:1', '2:1:1', '2:2:1']
    assert positions[0]['position_price'] == '2.01'
    assert positions[0]['currency'] == 'RUB'
    assert positions[0]['characteristics'][0]['Values'][0]['Value'] == 'М12'
    assert positions[1]['position_price'] is None
    assert tender['limitations'] == []


def test_missing_masked_or_conflicting_requirements_stay_explicit():
    tender = normalize_tender({'Id': '1'}, {'Id': '1', 'Title': 'Крепёж', 'Lots': [
        {'Customers': [{'Objects': [obj(2)]}], 'JointObjects': [obj(10)]},
        {}, {'JointObjects': [obj('#####', value='#####')]},
    ]})
    assert len(tender['positions']) == 2
    assert tender['positions'][-1]['quantity'] is None
    assert tender['positions'][-1]['characteristics'][0]['Values'][0]['Value'] == '#####'
    assert [reason['code'] for reason in tender['limitations']] == [
        'LOT_OBJECTS_CONFLICT', 'LOT_OBJECTS_MISSING', 'POSITION_REQUIREMENTS_INCOMPLETE',
    ]


def test_search_pages_in_provider_order_and_loads_one_escaped_id():
    class Opener:
        calls = []

        def open(self, request, timeout):
            self.calls.append((request.get_method(), request.full_url, request.data))
            assert request.get_header('X-kontur-apikey') == 'test-key'
            if request.get_method() == 'GET':
                assert request.full_url.endswith('/purchases/%D0%A1%D0%91-1')
                body = {'Id': 'СБ-1', 'Title': 'Тендер', 'Lots': []}
            else:
                query = json.loads(request.data)
                assert query['Text'] == ['болт М12']
                assert query['PurchaseStatuses'] == [1]
                start = datetime.fromisoformat(query['DateTimeFrom'])
                end = datetime.fromisoformat(query['DateTimeTo'])
                assert end - start == timedelta(days=365)
                assert start.utcoffset() == timedelta(hours=3)
                page = query['PageNumber']
                ids = [f'processed-{i}' for i in range(50)] if page == 0 else ['СБ-1', 'other']
                body = {'PageNumber': page, 'TotalCount': 52, 'Items': [{'Id': key} for key in ids]}
            return BytesIO(json.dumps(body).encode())

    opener = Opener()
    client = KonturClient('test-key', opener=opener, min_interval=0)
    tender = client.fetch_next('болт М12', lambda ids: {key for key in ids if key.startswith('processed-')})
    assert tender['id'] == 'СБ-1'
    assert [verb for verb, _, _ in opener.calls] == ['POST', 'POST', 'GET']
    assert tender['limitations'] == [{'code': 'TENDER_OBJECTS_MISSING'}]
