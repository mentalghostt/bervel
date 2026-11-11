from copy import deepcopy

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.matching import match_positions
from bervel.modules.ai_tender_radar.result_metrics import result_metrics
from test_ai_tender_radar_analysis import SelectingModel, need, row, stock, tender


def test_unrelated_service_with_no_evidence_is_a_known_absence_without_model_call():
    model = SelectingModel()
    matched = match_positions(tender(need('Услуги монтажа и технического обслуживания оборудования')),
                              stock(row('B12', 'Болт М12'), row('UNKNOWN', 'Неописанная деталь')),
                              model, 'service', lambda: None)
    [position] = matched.positions.values()
    assert position.by_sku == {}
    assert not position.uncertain
    assert model.calls == []


def test_only_stated_requirements_are_required_and_a_real_conflict_is_rejected():
    matched = match_positions(tender(need('Болт М12')),
                              stock(row('B12', 'Болт М12'), row('B16', 'Болт М16')),
                              SelectingModel(), 'stated-only', lambda: None)
    [position] = matched.positions.values()
    assert set(position.by_sku) == {'B12'}
    assert not position.uncertain


@pytest.mark.parametrize('free', ['10', '0'])
def test_explicit_unknown_coating_remains_uncertain(free):
    demand = tender(need('Болт М12', Characteristics=[{'Name': 'Покрытие', 'Values': [{'Value': 'Цинк'}]}]))
    matched = match_positions(demand, stock(row('B12', 'Болт М12', free=free)), SelectingModel(), 'coating', lambda: None)
    [position] = matched.positions.values()
    assert not position.by_sku and position.uncertain


def test_timeout_and_incomplete_source_are_not_negative_technical_verdicts():
    class Timeout:
        def generate(self, *args, **kwargs):
            raise CoreError('MODEL_TIMEOUT', 'No completed response')

    demand, warehouse = tender(need('Болт М12')), stock(row('B12', 'Болт М12'))
    assert match_positions(demand, warehouse, Timeout(), 'timeout', lambda: None).positions['1:0:1'].uncertain
    demand['limitations'].append({'code': 'POSITION_REQUIREMENTS_INCOMPLETE', 'position_id': '1:0:1'})
    assert match_positions(demand, warehouse, SelectingModel(), 'source', lambda: None).positions['1:0:1'].uncertain


def metric_card(*lines, limitations=()):
    return {'state': 'needs_data', 'catalog_match_percent': None,
            'tender': {'positions': [{'id': line['id'], 'lot': 1, 'quantity': '10', 'unit': 'шт'} for line in lines],
                       'limitations': list(limitations)},
            'analysis': {'positions': list(lines)}}


@pytest.mark.parametrize('covered', ['0', '1'])
def test_product_match_counts_technical_proof_despite_stock_and_saved_unknown_lot(covered):
    card = metric_card({'id': 'confirmed', 'matches': [{'sku': 'B12'}], 'covered': covered, 'stock_status': 'none'},
                       {'id': 'unknown', 'matches': [], 'covered': '0', 'stock_status': 'unconfirmed'})
    original = deepcopy(card)
    metrics = result_metrics(card)
    assert metrics['catalog_match_percent'] == '50'
    assert metrics['catalog_match_confirmed'] == 1
    assert metrics['catalog_match_unknown'] == 1
    assert metrics['catalog_match_total'] == 2
    assert metrics['catalog_match_incomplete'] is True
    assert card == original


@pytest.mark.parametrize(('reason', 'unknown', 'incomplete'), [
    ('MATCH_NOT_CONFIRMED', 1, True), ('NO_COMPATIBLE_SKU', 0, False),
])
def test_zero_confirmations_does_not_hide_the_difference_between_unknown_and_no_match(reason, unknown, incomplete):
    metrics = result_metrics(metric_card({'id': 'p', 'matches': [], 'covered': '0',
                                         'stock_status': 'none' if not unknown else 'unconfirmed', 'stock_reason': reason}))
    assert metrics['catalog_match_percent'] == '0'
    assert metrics['catalog_match_unknown'] == unknown
    assert metrics['catalog_match_incomplete'] is incomplete


def test_incomplete_composition_is_explicit_even_if_every_disclosed_row_matches():
    card = metric_card({'id': 'p', 'matches': [{'sku': 'B12'}], 'covered': '0'},
                       limitations=[{'code': 'LOT_OBJECTS_MISSING', 'lot': 2}])
    metrics = result_metrics(card)
    assert metrics['catalog_match_percent'] == '100'
    assert metrics['catalog_match_unknown'] == 0
    assert metrics['catalog_match_total'] == 1
    assert metrics['catalog_match_incomplete'] is True


def test_no_analysis_has_no_technical_percentage_even_with_a_legacy_lot_metric():
    card = metric_card({'id': 'p'})
    card['analysis'] = None
    card['catalog_match_percent'] = '100'
    metrics = result_metrics(card)
    assert metrics['catalog_match_percent'] is None
    assert metrics['catalog_match_confirmed'] == 0
    assert metrics['catalog_match_unknown'] == metrics['catalog_match_total'] == 1
