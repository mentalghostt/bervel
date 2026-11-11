"""Blind warehouse coverage corpus; private truth never enters the HTTP API."""
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import json

import pytest

from kontur_simulator import coverage_corpus, create_server
from test_ai_radar_selection_http import selection_server, search


TODAY = datetime.fromisoformat('2026-11-05T00:00:00+03:00')


@pytest.fixture
def stock_csv():
    rows = ['sku;product;free_quantity;unit;unit_cost;currency;as_of;data_kind;standard;thread_diameter_mm;length_mm']
    for index in range(24):
        diameter, length = 8 + index, 40 + index * 5
        rows.append(f'S-{index};Болт М{diameter}×{length} ГОСТ 7798-70;900;шт;12.25;RUB;2026-11-04T00:00:00+03:00;reported;ГОСТ 7798-70;{diameter};{length}')
    return ('\n'.join(rows) + '\n').encode()


def corpus(stock_csv, **options):
    return coverage_corpus(TODAY, 7319, 'mixed', stock_csv=stock_csv, **options)


def test_coverage_has_150_verified_positives_and_over_1000_hard_negatives(stock_csv):
    purchases, truth = corpus(stock_csv)
    positives = [row for row in truth.values() if row['positive']]
    assert len(positives) == 150
    assert len(truth) - len(positives) >= 1000
    assert Counter(row['target_coverage_percent'] for row in positives) == {'100': 100, '90': 25, '80': 25}
    assert all(abs(Decimal(row['coverage_percent']) - Decimal(row['target_coverage_percent'])) < Decimal('0.01') for row in positives)
    assert {'low_2', 'low_6', 'wrong_standard', 'wrong_size', 'wrong_unit', 'mixed', 'soap', 'company_name'} <= {row['case'] for row in truth.values()}
    assert all(row['stock_sha256'] == next(iter(truth.values()))['stock_sha256'] for row in truth.values())
    for label, expected in [('low_2', Decimal('2')), ('low_6', Decimal('6'))]:
        assert all(abs(Decimal(row['coverage_percent']) - expected) < Decimal('.01') for row in truth.values() if row['case'] == label)
    assert len(purchases) == len(truth)


def test_cards_use_normal_kontur_fields_decimal_prices_and_delivery(stock_csv):
    from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender
    purchases, truth = corpus(stock_csv)
    for key, purchase in purchases.items():
        card = purchase.card()
        assert Decimal(str(card['InitialSum']['Price'])) == sum(Decimal(position['PositionPrice']) for lot in card['Lots'] for position in lot['JointObjects'])
        tender = normalize_tender(purchase.brief(), card)
        assert not tender['limitations']
        assert 'Поставка до ' in card['Lots'][0]['Customers'][0]['EndContractDate']
        if truth[key]['positive']:
            assert all(position['quantity'] and position['unit'] for position in tender['positions'])
        assert 'Данные о расходах подтверждены:' in purchase.document_text()
    for positive in [True, False]:
        assert any(len(purchase.card()['Lots']) > 1 for key, purchase in purchases.items() if truth[key]['positive'] is positive)


def test_seeds_holdout_and_snippets_do_not_encode_labels(stock_csv):
    purchases, truth = corpus(stock_csv)
    same, same_truth = corpus(stock_csv)
    assert truth == same_truth and list(purchases) == list(same)
    absent, absent_truth = coverage_corpus(TODAY, 7319, 'omitted', stock_csv=stock_csv)
    assert truth == absent_truth and list(purchases) == list(absent)
    holdout, holdout_truth = corpus(stock_csv, split='holdout')
    other, _ = coverage_corpus(TODAY, 99173, 'mixed', stock_csv=stock_csv)
    assert not set(purchases) & set(holdout) and not set(purchases) & set(other)
    assert Counter(row['case'] for row in truth.values()) == Counter(row['case'] for row in holdout_truth.values())
    # Every visible title family must occur in both classes. IDs are opaque random values.
    titles = {}
    for key, purchase in purchases.items():
        titles.setdefault(purchase.title, set()).add(truth[key]['positive'])
        assert len(key) == 36 and key.startswith('SIM-')
        wire = json.dumps([purchase.brief(), purchase.card(), purchase.document_text()], ensure_ascii=False).casefold()
        assert not any(label in wire for label in ('ground_truth', 'target_coverage', 'positive', 'holdout', 'stock_sha256'))
    assert all(flags == {True, False} for flags in titles.values())
    assert sum(truth[key]['positive'] for key in list(purchases)[:100]) not in (0, 100)


def test_wire_pagination_dedup_and_201_detail_requests_with_configured_quota(stock_csv):
    with selection_server(profile='coverage', stock_csv=stock_csv, request_quota=1600) as (server, request):
        keys = []
        total = len(server.purchases)
        for page in range((total + 49) // 50):
            query = search(page=page)
            query['Text'] = []
            status, result = request('POST', '/external/v1/search', query)
            assert status == 200 and result['TotalCount'] == total
            assert len(result['Items']) <= 50
            keys.extend(item['Id'] for item in result['Items'])
        assert len(keys) == total == len(set(keys))
        for key in keys[:201]:
            status, card = request('GET', '/external/v1/purchases/' + key)
            assert status == 200 and card['Id'] == key and card['Lots']
        limits = request('GET', '/external/v1/limitGroups')[1]
        assert limits[1]['AvailableRequests'] == 1399 and limits[1]['TotalLimit'] == 1600


def test_more_than_200_full_coverage_details_are_configurable(stock_csv):
    _, truth = corpus(stock_csv, positive_count=330, negative_count=1100)
    assert sum(row['positive'] and row['coverage_percent'] == '100' for row in truth.values()) == 220


def test_invalid_configuration_fails_before_listener(stock_csv):
    for options in ({'positive_count': 0}, {'negative_count': -1}, {'split': 'truth-in-http'}):
        with pytest.raises(ValueError):
            corpus(stock_csv, **options)
    with pytest.raises(ValueError, match='stock'):
        create_server(0, profile='coverage')


def test_actual_warehouse_is_unmodified_and_matches_3076_rows():
    path = Path('.dev/local/radar-unified-2026-11-05/simulator/actual-stock.private.csv')
    if not path.exists():
        pytest.skip('Installed warehouse snapshot is private local acceptance input')
    from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
    before = path.read_bytes()
    assert len(read_stock(before)['rows']) == 3076
    _, truth = corpus(before)
    assert sum(row['positive'] for row in truth.values()) == 150
    assert all(row['coverage_percent'] is not None for row in truth.values() if row['positive'])
    assert path.read_bytes() == before
