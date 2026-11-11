"""Discrete coverage checks independent of selector/prefilter implementation."""
from collections import Counter
from datetime import datetime
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from kontur_simulator import coverage_corpus, create_server, selection_corpus


HERE = Path('.dev/local/radar-unified-2026-11-05/simulator-small-stock')
TODAY = datetime.fromisoformat('2026-11-05T00:00:00+03:00')
SEEDS = [(88117, 'development'), (32533, 'development'), (71993, 'holdout')]


def small_stock():
    header = 'sku;product;free_quantity;unit;unit_cost;currency;as_of;data_kind;standard;thread_diameter_mm;length_mm\n'
    values = [0, 1, 2, 7, 29, 49, 50, 74, 99, 101, 127, 151, 173, 200]
    lines = [f'SMALL-{index};Болт М{8+index}×{50+index*5} ГОСТ 7798-70;{amount};шт;13.42;RUB;2026-11-05T00:00:00+03:00;mock;ГОСТ 7798-70;{8+index};{50+index*5}'
             for index, amount in enumerate(values)]
    return (header + '\n'.join(lines) + '\n').encode()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()


def test_small_integer_quantities_never_label_below_80_as_positive():
    purchases, truth = coverage_corpus(TODAY, 88117, 'mixed', stock_csv=small_stock(), discrete_quantities=True)
    assert len(purchases) == len(truth) == 1350
    positives = [row for row in truth.values() if row['positive']]
    assert Counter(row['target_coverage_percent'] for row in positives) == {'100': 100, '90': 25, '80': 25}
    for row in positives:
        actual, target = Decimal(row['coverage_percent']), Decimal(row['target_coverage_percent'])
        assert actual >= 80 and target <= actual < target + 2
    for case, target in [('low_2', Decimal(2)), ('low_6', Decimal(6))]:
        assert all(target - Decimal('.01') < Decimal(row['coverage_percent']) <= target
                   for row in truth.values() if row['case'] == case)
    assert all(row['source_skus'] for row in truth.values())


def test_discrete_setting_is_explicit_and_never_appears_in_wire():
    purchases, truth = coverage_corpus(TODAY, 88117, 'mixed', stock_csv=small_stock(), discrete_quantities=True)
    for purchase in purchases.values():
        wire = encoded([purchase.brief(), purchase.card(), purchase.document_text()]).decode().casefold()
        assert not any(label in wire for label in ['discrete_quantities', 'target_coverage_percent', 'stock_sha256', 'ground_truth', 'positive'])
    with pytest.raises(ValueError):
        coverage_corpus(TODAY, 88117, 'mixed', stock_csv=small_stock(), discrete_quantities='yes')


def test_runtime_server_accepts_explicit_discrete_mode_with_50_per_page():
    with create_server(0, profile='coverage', today=TODAY, seed=88117, stock_csv=small_stock(),
                       discrete_quantities=True, request_quota=3000) as server:
        page = server.search({'Text': [], 'PageNumber': 0, 'DateTimeFrom': '2026-01-01T00:00:00+03:00',
                              'DateTimeTo': TODAY.isoformat()})
        assert page['TotalCount'] == 1350 and len(page['Items']) == 50
        assert all(Decimal(row['coverage_percent']) >= 80 for row in server.ground_truth.values() if row['positive'])
        assert server.limit_groups()[1]['TotalLimit'] == 3000


def test_default_output_is_byte_compatible_with_frozen_v1():
    spec = importlib.util.spec_from_file_location('small_stock_frozen_v1', HERE / 'kontur_simulator.v1.frozen.py')
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    # Sufficient old-style stock so the legacy ceil tolerance remains meaningful.
    stock = small_stock().decode()
    for amount in [0, 1, 2, 7, 29, 49, 50, 74, 99, 101, 127, 151, 173, 200]:
        stock = stock.replace(f';{amount};шт;', ';1000000;шт;')
    stock = stock.encode()
    args = [TODAY, 7319, 'mixed']
    current, labels = coverage_corpus(*args, stock_csv=stock)
    previous, previous_labels = old.coverage_corpus(*args, stock_csv=stock)
    assert encoded(labels) == encoded(previous_labels)
    assert encoded([[item.brief(), item.card(), item.document_text()] for item in current.values()]) == encoded([[item.brief(), item.card(), item.document_text()] for item in previous.values()])
    current_selection, selection_labels = selection_corpus(*args)
    old_selection, old_labels = old.selection_corpus(*args)
    assert encoded(selection_labels) == encoded(old_labels)
    assert encoded([[item.brief(), item.card(), item.document_text()] for item in current_selection.values()]) == encoded([[item.brief(), item.card(), item.document_text()] for item in old_selection.values()])


@pytest.mark.parametrize('seed,split', SEEDS)
def test_actual_new_3076_stock_has_150_genuine_positives(seed, split):
    path = HERE / 'actual-small-stock.private.csv'
    if not path.exists():
        pytest.skip('Private installed stock input not available')
    from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
    stock = path.read_bytes()
    assert hashlib.sha256(stock).hexdigest() == '687ba4ddbe907e5dab680a0eadb022bf348abbc25e5807cb7db642b96ee9ec32'
    parsed = read_stock(stock)
    assert len(parsed['rows']) == 3076 and parsed['data_kind'] == 'mock'
    assert all(0 <= Decimal(row['free_quantity']) <= 200 for row in parsed['rows'])
    purchases, truth = coverage_corpus(TODAY, seed, 'mixed', stock_csv=stock, split=split, discrete_quantities=True)
    positives = [row for row in truth.values() if row['positive']]
    assert len(positives) == 150
    assert all(Decimal(row['coverage_percent']) >= Decimal(row['target_coverage_percent']) >= 80 for row in positives)
    assert Counter(row['case'] for row in positives) == {'full': 100, 'near_90': 25, 'near_80': 25}
    assert all(len(row['stock_sha256']) == 64 for row in truth.values())
    assert path.read_bytes() == stock
    assert len(purchases) == 1350
