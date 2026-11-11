from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import re

from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender
from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
from bervel.modules.ai_tender_radar.prefilter import CatalogPrefilter
from bervel.modules.ai_tender_radar.result_metrics import catalog_match_percent
from kontur_simulator import realistic_corpus


def _assortment(purchase):
    positions = purchase.coverage_positions
    if purchase.requirements_variant == 'empty_objects':
        positions = positions[len(positions) // purchase.realistic_lot_count:]
    quantities = defaultdict(Decimal)
    for position in positions:
        name = ' '.join(re.findall(r'\w+', position['Name'].casefold().replace('ё', 'е')))
        unit = position['Okei'].casefold()
        if unit in {'килограмм', '166'}:
            unit = 'кг'
        characteristics = tuple(sorted((item['Name'], item['Values'][0]['Value'], item['Values'][0]['Okei'])
                                       for item in position['Characteristics']))
        quantities[name, unit, characteristics] += Decimal(position['Quantity'])
    return frozenset(quantities), frozenset(quantities.items())


def test_realistic_purchase_corpus_has_large_mixed_lots_and_independent_catalog_match():
    stock = ('sku;product;free_quantity;unit;unit_cost;currency;data_kind;standard;'
             'thread_diameter_mm;length_mm;weight_per_piece_kg\n'
             'B1;Болт М12×50 ГОСТ 7798-70;40;шт;20;RUB;reported;ГОСТ 7798-70;12;50;0.1\n').encode()
    purchases, truth = realistic_corpus(datetime(2026, 11, 6, tzinfo=timezone.utc), 71993, stock_csv=stock)
    assert len(purchases) == 5000
    assert sha256('\n'.join(purchases).encode()).hexdigest() == 'ce4c911db88f2b0224e8e9310f2c7c18ced246c368f132689f834d830ec22fa9'
    assert len({purchase.title.casefold() for purchase in purchases.values()}) == 5000
    assert all(purchase.key not in purchase.title for purchase in purchases.values())
    assortments = {key: _assortment(purchase) for key, purchase in purchases.items()}
    assert len({basket for _, basket in assortments.values()}) == 5000
    assert len({assortments[key][0] for key, item in truth.items() if not item['full_catalog_match']}) == 4970
    positives = [purchases[key] for key, item in truth.items() if item['full_catalog_match']]
    assert len(positives) == 30
    assert max(item['lots'] for item in truth.values()) >= 30
    assert max(item['required_positions'] for item in truth.values()) >= 150
    assert max(p.total_price for p in purchases.values()) >= 9_000_000
    assert min(p.total_price for p in purchases.values()) <= 550_000
    matcher = CatalogPrefilter(read_stock(stock), {})
    units = set()
    for purchase in positives:
        raw = purchase.card()
        tender = normalize_tender(purchase.brief(), raw)
        assert catalog_match_percent(tender, matcher.catalog_match) == '100'
        assert matcher.check(tender)['state'] == 'reject'  # Little stock, full catalog match.
        units.update(p['unit'] for p in tender['positions'])
        assert len(tender['positions']) == truth[purchase.key]['required_positions']
        assert all(Decimal(p['item_price']) * Decimal(p['quantity']) == Decimal(p['position_price']) for p in tender['positions'])
        assert Decimal(str(raw['InitialSum']['Price'])) == sum(Decimal(p['position_price']) for p in tender['positions'])
    assert {'шт', 'кг'} <= units
