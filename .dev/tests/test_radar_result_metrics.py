from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bervel.modules.ai_tender_radar.result_metrics import catalog_match_percent, result_metrics


def test_lots_require_all_positions_and_do_not_consider_stock_quantity():
    positions = [{'id': 'bolt', 'lot': 1}, {'id': 'service', 'lot': 1}, {'id': 'nut', 'lot': 2}]
    tender = {'positions': positions, 'source_card': {'Lots': [{}, {}]}}
    matches = {'bolt': True, 'service': False, 'nut': True}
    assert Decimal(catalog_match_percent(tender, lambda p: matches[p['id']])) == 50
    tender['source_card']['Lots'].append({})
    assert catalog_match_percent(tender, lambda p: matches[p['id']]) is None


def test_price_stage_and_saved_match_are_derived_from_card_source():
    card = {
        'brief': {'MaxPrice': 900000, 'Currency': 'RUB', 'ApplicationDeadline': '2026-12-01T00:00:00Z'},
        'tender': {'source_card': {'InitialSum': {'Price': '1000000.25', 'Currency': 'RUB'}}},
        'catalog_match_percent': '100',
    }
    now = datetime(2026, 11, 6, tzinfo=timezone.utc)
    assert result_metrics(card, now=now) == {
        'price': '1000000.25', 'currency': 'RUB', 'stage': 'accepting', 'catalog_match_percent': None,
        'catalog_match_confirmed': 0, 'catalog_match_unknown': 0, 'catalog_match_total': 0,
        'catalog_match_incomplete': True,
        'stock_coverage_percent': None, 'stock_coverage_incomplete': False,
    }
    assert result_metrics(card, now=datetime(2026, 12, 2, tzinfo=timezone.utc))['stage'] == 'closed'
    card['brief']['IsCancelled'] = True
    assert result_metrics(card, now=now)['stage'] == 'cancelled'


@pytest.mark.parametrize(('lots', 'matching', 'covered', 'catalog', 'expected'), [
    (4, 1, 1, '25', '100'),
    (10, 10, 1, '100', '10'),
    (6, 6, 0, '100', '0'),
    (4, 0, 0, '0', '0'),
])
def test_saved_card_shows_coverage_of_catalog_lots_even_with_incomplete_documents(lots, matching, covered, catalog, expected):
    positions = [{'id': str(i), 'lot': i + 1, 'unit': 'шт', 'quantity': '10'} for i in range(lots)]
    lines = [
        dict(position, matches=[{'sku': 'B'}] if i < matching else [],
             stock_status='full' if i < covered else 'unconfirmed', covered='10' if i < covered else '0')
        for i, position in enumerate(positions)
    ]
    card = {'state': 'needs_data', 'catalog_match_percent': catalog,
            'tender': {'positions': positions, 'limitations': [{'code': 'DOCUMENT_EXTRACTION_UNKNOWN'}]},
            'analysis': {'coverage_percent': None, 'positions': lines}}
    assert result_metrics(card)['stock_coverage_percent'] == expected


def test_saved_coverage_replaces_old_lot_count_and_weights_pieces_across_units():
    positions = [
        {'id': 'bolt', 'lot': 1, 'unit': 'шт', 'quantity': '200'},
        {'id': 'mass', 'lot': 1, 'unit': 'кг', 'quantity': '10'},
        {'id': 'kit', 'lot': 2, 'unit': 'комплект', 'quantity': '10'},
    ]
    lines = [
        {'id': 'bolt', 'covered': '100', 'matches': [{'sku': 'B'}]},
        {'id': 'mass', 'covered': '5', 'matches': [{'sku': 'M'}]},
        {'id': 'kit', 'covered': '10', 'matches': [{'sku': 'K'}]},
    ]
    card = {'state': 'needs_data', 'stock_coverage_percent': '50', 'catalog_match_percent': '100',
            'tender': {'positions': positions}, 'analysis': {'positions': lines}}
    stock_rows = {'M': {'attributes': {'weight_per_piece_kg': '0.1'}}, 'K': {'attributes': {'pieces_per_set': '10'}}}
    metrics = result_metrics(card, historical_stock_rows=lambda: stock_rows)
    assert metrics['stock_coverage_percent'] == '62.5'  # 250 of 400 pieces
    assert metrics['stock_coverage_incomplete'] is False
    partial = result_metrics(card, historical_stock_rows=lambda: {'K': stock_rows['K']})
    assert Decimal(partial['stock_coverage_percent']).quantize(Decimal('.01')) == Decimal('66.67')
    assert partial['stock_coverage_incomplete'] is True
