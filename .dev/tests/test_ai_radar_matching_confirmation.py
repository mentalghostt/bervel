from copy import deepcopy

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar import calculation
from bervel.modules.ai_tender_radar.matching import match_positions
from bervel.modules.ai_tender_radar.production import evaluate_fulfillment
from test_ai_radar_production import NOW, case
from test_ai_tender_radar_analysis import SelectingModel, need, row, stock, tender


def bolt_case():
    demand = tender(need('Болт М12×95 ГОСТ 7798-70', 25680, ItemPrice=10))
    warehouse = stock(row('B12', 'Болт М12', free='69', length_mm='95', standard='ГОСТ 7798-70'),
                      row('UNKNOWN', 'Неописанная деталь', free='50000'))
    return demand, warehouse


def analyze(demand, warehouse, model=None):
    matching = match_positions(demand, warehouse, model or SelectingModel(), 'confirmation', lambda: None)
    return matching, calculation.calculate_analysis(demand, warehouse, matching)


def test_unrelated_unknown_sku_does_not_invalidate_verified_partial_stock():
    demand, warehouse = bolt_case()
    matching, result = analyze(demand, warehouse)
    [line] = result['positions']
    assert not matching.positions[line['id']].uncertain
    assert (line['required'], line['covered'], line['shortage']) == ('25680', '69', '25611')
    assert line['stock_status'] == 'partial'
    assert line['stock_reason'] == 'INSUFFICIENT_STOCK'
    assert [match['sku'] for match in line['matches']] == ['B12']
    assert line['economics']['scope'] == 'confirmed'
    assert line['economics']['profit'] == '345'


def test_model_omitting_an_alternative_does_not_invalidate_selected_proof():
    demand, warehouse = bolt_case()
    warehouse['rows'].append(dict(warehouse['rows'][0], sku='OTHER', free_quantity='100000'))

    def omit_alternative(answer):
        answer['positions'][0]['matches'] = [answer['positions'][0]['matches'][0]]
        return answer

    matching, result = analyze(demand, warehouse, SelectingModel(omit_alternative))
    [line] = result['positions']
    assert not matching.positions[line['id']].uncertain
    assert line['shortage'] == '25611'
    assert [allocation['sku'] for allocation in line['allocations']] == ['B12']


def test_confirmed_shortage_enters_real_production_calculation():
    demand, warehouse = bolt_case()
    terms, _, _, profile = case(covered='69', required='25680', rate='10000')
    demand['terms'] = terms['terms']
    profile['products'][0]['sku'] = 'B12'
    _, analysis = analyze(demand, warehouse)
    fulfillment = evaluate_fulfillment(demand, warehouse, analysis, profile, now=NOW)
    assert fulfillment['status'] == 'feasible'
    assert fulfillment['production_allocations'][0]['quantity'] == '25611'
    assert fulfillment['full_economics']['profit'] == '128400'


def test_fifteen_positions_share_stock_once_despite_unknown_catalogue_row():
    demand, warehouse = bolt_case()
    demand['positions'] = [dict(deepcopy(demand['positions'][0]), id=f'p{index}') for index in range(15)]
    _, analysis = analyze(demand, warehouse)
    assert [line['covered'] for line in analysis['positions']] == ['69'] + ['0'] * 14
    assert [line['shortage'] for line in analysis['positions']] == ['25611'] + ['25680'] * 14
    assert {line['stock_status'] for line in analysis['positions']} == {'partial', 'none'}


@pytest.mark.parametrize('missing', ['length_mm', 'standard'])
def test_each_required_fact_remains_mandatory(missing):
    demand, warehouse = bolt_case()
    warehouse['rows'][0]['attributes'].pop(missing)
    matching, analysis = analyze(demand, warehouse)
    [line] = analysis['positions']
    assert matching.positions[line['id']].uncertain
    assert line['matches'] == []
    assert line['shortage'] is None
    assert line['stock_status'] == 'unconfirmed'


@pytest.mark.parametrize('unknown', ['stock_quantity', 'required_quantity', 'unit', 'requirements'])
def test_unknown_selected_data_stays_unconfirmed(unknown):
    demand, warehouse = bolt_case()
    if unknown == 'stock_quantity':
        warehouse['rows'][0]['free_quantity'] = None
    elif unknown == 'required_quantity':
        demand['positions'][0]['quantity'] = None
    elif unknown == 'unit':
        demand['positions'][0]['unit'] = None
    else:
        demand['limitations'].append({'code': 'INCOMPLETE', 'position_id': demand['positions'][0]['id']})
    _, analysis = analyze(demand, warehouse)
    [line] = analysis['positions']
    assert line['stock_status'] == 'unconfirmed'
    assert line['shortage'] is None


def test_no_selected_sku_remains_uncertain_after_model_response():
    demand, warehouse = bolt_case()

    def omit_all(answer):
        answer['positions'][0]['matches'] = []
        return answer

    _, analysis = analyze(demand, warehouse, SelectingModel(omit_all))
    [line] = analysis['positions']
    assert line['stock_status'] == 'unconfirmed'
    assert line['shortage'] is None


def test_model_failure_remains_unknown_and_is_reported():
    class FailedModel:
        def generate(self, *args, **kwargs):
            raise CoreError('MODEL_TIMEOUT', 'Model did not finish')

    _, analysis = analyze(*bolt_case(), FailedModel())
    [line] = analysis['positions']
    assert analysis['model_error']['code'] == 'MODEL_TIMEOUT'
    assert line['stock_status'] == 'unconfirmed'
    assert line['shortage'] is None
