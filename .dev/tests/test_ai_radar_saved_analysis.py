from copy import deepcopy

import pytest

from bervel.modules.ai_tender_radar import calculation
from bervel.modules.ai_tender_radar.production import evaluate_fulfillment
from test_ai_radar_matching_confirmation import analyze, bolt_case
from test_ai_radar_production import NOW, case
from test_ai_tender_radar_analysis import need, row, stock, tender


def saved_case():
    demand, warehouse = bolt_case()
    _, analysis = analyze(demand, warehouse)
    # Shape actually persisted by the old matcher despite complete SKU evidence.
    analysis['positions'][0].update(stock_status='unconfirmed', stock_reason='MATCH_NOT_CONFIRMED', shortage=None)
    analysis.update(coverage_percent=None, coverage_reason='STOCK_DATA_NOT_CONFIRMED')
    return demand, warehouse, analysis


def test_saved_complete_evidence_recovers_shortage_without_mutating_snapshots():
    demand, warehouse, saved = saved_case()
    saved.update(model='saved-model', model_error={'code': 'MODEL_TIMEOUT', 'message': 'Another batch failed'}, mock=True)
    snapshots = deepcopy((demand, warehouse, saved))
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    [line] = result['positions']
    assert (line['covered'], line['shortage'], line['stock_status']) == ('69', '25611', 'partial')
    assert line['matches'][0]['sku'] == 'B12'
    assert result['economics']['profit'] == '345'
    assert result['model'] == 'saved-model'
    assert result['model_error'] == {'code': 'MODEL_TIMEOUT', 'message': 'Another batch failed'}
    assert result['mock'] is True
    assert result['status'] == 'partial'
    assert (demand, warehouse, saved) == snapshots
    line['matches'][0]['evidence'][0]['stock_value'] = 'changed projection'
    result['model_error']['message'] = 'changed projection'
    assert (demand, warehouse, saved) == snapshots


@pytest.mark.parametrize('corrupt', ['missing_requirement', 'duplicate_requirement', 'field', 'value',
                                   'tender_value', 'label', 'absent_sku', 'unknown_sku', 'empty', 'legacy'])
def test_saved_incomplete_or_forged_evidence_cannot_create_confirmed_match(corrupt):
    demand, warehouse, saved = saved_case()
    match = saved['positions'][0]['matches'][0]
    if corrupt == 'missing_requirement':
        match['evidence'].pop()
    elif corrupt == 'duplicate_requirement':
        match['evidence'][-1] = deepcopy(match['evidence'][0])
    elif corrupt == 'field':
        match['evidence'][0]['stock_field'] = 'invented'
    elif corrupt == 'value':
        match['evidence'][0]['stock_value'] = 'invented'
    elif corrupt == 'tender_value':
        match['evidence'][0]['tender_value'] = 'гайка'
    elif corrupt == 'label':
        match['evidence'][0]['requirement'] = 'invented'
    elif corrupt == 'absent_sku':
        match['sku'] = 'MISSING'
    elif corrupt == 'unknown_sku':
        match['sku'] = 'UNKNOWN'
    elif corrupt == 'empty':
        match['evidence'] = []
    else:
        match['evidence'] = ['verified']
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    [line] = result['positions']
    assert line['matches'] == []
    assert line['allocations'] == []
    assert line['shortage'] is None
    assert line['stock_status'] == 'unconfirmed'
    assert result['economics']['profit'] is None


def test_refresh_uses_saved_selection_only_and_does_not_select_new_alternatives():
    demand, warehouse, saved = saved_case()
    warehouse['rows'].append(dict(warehouse['rows'][0], sku='NEW', free_quantity='100000'))
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    [line] = result['positions']
    assert line['covered'] == '69'
    assert line['shortage'] == '25611'
    assert [match['sku'] for match in line['matches']] == ['B12']


def test_refresh_revalidates_historical_facts_even_if_saved_text_is_consistent():
    demand, warehouse, saved = saved_case()
    # The inline M12 proof still agrees, but another source field conflicts.
    warehouse['rows'][0]['attributes']['thread_diameter_mm'] = '16'
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    assert result['positions'][0]['matches'] == []
    assert result['positions'][0]['shortage'] is None


@pytest.mark.parametrize('unknown', ['quantity', 'stock', 'unit', 'requirements'])
def test_refresh_keeps_unknown_quantities_units_and_requirements_unknown(unknown):
    demand, warehouse, saved = saved_case()
    if unknown == 'quantity':
        demand['positions'][0]['quantity'] = None
    elif unknown == 'stock':
        warehouse['rows'][0]['free_quantity'] = None
    elif unknown == 'unit':
        demand['positions'][0]['unit'] = None
    else:
        demand['limitations'].append({'code': 'INCOMPLETE', 'position_id': demand['positions'][0]['id']})
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    assert result['positions'][0]['stock_status'] == 'unconfirmed'
    assert result['positions'][0]['shortage'] is None


@pytest.mark.parametrize('reason', ['MODEL_NOT_COMPLETED', 'MODEL_CONTEXT_LIMIT'])
def test_refresh_does_not_repair_unfinished_model_selection(reason):
    demand, warehouse, saved = saved_case()
    saved['positions'][0].update(matches=[], allocations=[], covered='0', stock_reason=reason)
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    assert result['positions'][0]['matches'] == []
    assert result['positions'][0]['stock_reason'] == reason
    assert result['positions'][0]['shortage'] is None


def test_refresh_preserves_confirmed_absence_instead_of_matching_catalogue_anew():
    demand = tender(need('Болт М12'))
    warehouse = stock(row('B16', 'Болт М16'))
    _, saved = analyze(demand, warehouse)
    warehouse['rows'].append(row('B12', 'Болт М12'))
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    [line] = result['positions']
    assert (line['covered'], line['shortage'], line['stock_status']) == ('0', '10', 'none')
    assert line['matches'] == []
    assert line['stock_reason'] == 'NO_COMPATIBLE_SKU'


def test_refresh_redistributes_shared_stock_once_across_all_positions():
    demand = tender(need('Болт М12', 10, ItemPrice=10), need('A', 10, ItemPrice=10))
    warehouse = stock(row('A', 'Болт М12'), row('B', 'Болт М12'), row('UNKNOWN', 'Неописанная деталь'))
    _, saved = analyze(demand, warehouse)
    for line in saved['positions']:
        line.update(stock_status='unconfirmed', stock_reason='MATCH_NOT_CONFIRMED', shortage=None)
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    assert result['coverage_percent'] == '100'
    assert [line['allocations'][0]['sku'] for line in result['positions']] == ['B', 'A']
    assert [line['shortage'] for line in result['positions']] == ['0', '0']


def test_refreshed_shortage_flows_to_production_without_new_matching():
    demand, warehouse, saved = saved_case()
    terms, _, _, profile = case(covered='69', required='25680', rate='10000')
    demand['terms'] = terms['terms']
    profile['products'][0]['sku'] = 'B12'
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    fulfillment = evaluate_fulfillment(demand, warehouse, result, profile, now=NOW)
    assert fulfillment['status'] == 'feasible'
    assert fulfillment['production_allocations'][0]['quantity'] == '25611'


def test_saved_zero_stock_match_remains_available_to_production():
    demand, warehouse = bolt_case()
    warehouse['rows'][0]['free_quantity'] = '0'
    _, saved = analyze(demand, warehouse)
    result = calculation.refresh_saved_analysis(demand, warehouse, saved)
    [line] = result['positions']
    assert line['matches'][0]['sku'] == 'B12'
    assert line['stock_reason'] == 'NO_STOCK'
    assert line['shortage'] == '25680'
