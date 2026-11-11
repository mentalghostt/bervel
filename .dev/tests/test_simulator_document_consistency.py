"""Realistic document summaries must describe the final generated assortment."""
from datetime import datetime, timezone

import pytest

from kontur_simulator import realistic_corpus


@pytest.fixture(scope='module')
def realistic_documents():
    stock = ('sku;product;free_quantity;unit;unit_cost;currency;data_kind;standard;'
             'thread_diameter_mm;length_mm;weight_per_piece_kg\n')
    stock += '\n'.join(
        f'B{index};Болт М{8 + index}×{50 + index * 5} ГОСТ 7798-70;40;шт;20;RUB;'
        f'reported;ГОСТ 7798-70;{8 + index};{50 + index * 5};0.1'
        for index in range(24))
    return realistic_corpus(datetime(2026, 11, 6, tzinfo=timezone.utc), 71993,
                            stock_csv=stock.encode(), positive_count=3, negative_count=77)


def test_realistic_summary_never_mentions_a_product_absent_from_final_document(realistic_documents):
    purchases, _ = realistic_documents
    stale = []
    for purchase in purchases.values():
        final_names = {position['Name'] for position in purchase.coverage_positions}
        rendered_summary = purchase.document_text().splitlines()[1]
        for mention in rendered_summary.split('; '):
            if mention and mention not in final_names:
                stale.append((purchase.key, mention))
    assert stale == []


def test_realistic_document_correction_keeps_partial_lot_scenarios(realistic_documents):
    purchases, _ = realistic_documents
    partial = [purchase for purchase in purchases.values() if purchase.requirements_variant == 'empty_objects']
    assert len(partial) == 5
    for purchase in partial:
        lots = purchase.card()['Lots']
        assert lots[0]['JointObjects'] == []
        assert lots[0]['Customers'][0]['Objects'] == []
        assert any(lot['JointObjects'] or lot['Customers'][0]['Objects'] for lot in lots[1:])
        assert len(purchase.document_text().split('Наименование: ')) - 1 == len(purchase.coverage_positions)
