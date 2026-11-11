from copy import deepcopy
import importlib

import pytest

from bervel.modules.ai_tender_radar.calculation import calculate_analysis
from bervel.modules.ai_tender_radar.matching import match_positions
from test_ai_tender_radar_analysis import SelectingModel, need, row, stock, tender


def card_case(*, free='4', cost='5', unit='шт', quantity=10, price=10, stock_unit='шт', currency='RUB', **attributes):
    demand = tender(need('Болт М12', quantity, unit, ItemPrice=price))
    warehouse = stock(row('B12', 'Болт М12', free=free, unit=stock_unit, cost=cost,
                          currency=currency, **attributes))
    matching = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    analysis = calculate_analysis(demand, warehouse, matching)
    return {'tender': demand, 'analysis': analysis, 'evaluated_at': '2026-11-02T00:00:00+00:00',
            'fulfillment': {'version': 4}, 'assessment': {'score': None, 'version': 2},
            'review': {'prompt_version': 'card-review-v3', 'recommendation': 'Старая ошибка'}}, {}, warehouse


def present(data):
    return importlib.import_module('bervel.modules.ai_tender_radar.presentation').present_card(*data)


def test_projection_preserves_saved_card_and_returns_comparable_prices():
    data = card_case()
    original = deepcopy(data)
    projected = present(data)
    assert data == original
    assert projected['assessment'] == original[0]['assessment']
    assert projected['evaluated_at'] == original[0]['evaluated_at']
    assert projected['analysis']['positions'][0]['unit_prices'] == {
        'stock_cost': '5', 'tender_price': '10', 'currency': 'RUB', 'unit': 'шт'}
    assert projected['opportunity']['score'] == 40
    assert projected['projection']['review_stale'] is True
    assert 2 <= len(projected['report_summary']['paragraphs']) <= 4
    assert projected['report_summary']['decision'] == 'review'


def test_zero_stock_still_shows_verified_catalog_cost():
    result = present(card_case(free='0'))
    assert result['analysis']['positions'][0]['unit_prices']['stock_cost'] == '5'
    assert result['opportunity']['score'] == 0


def test_cost_in_pieces_is_converted_to_tender_kilograms_exactly():
    result = present(card_case(free='20', unit='кг', quantity=2, price=100, weight_per_piece_kg='0.05'))
    assert result['analysis']['positions'][0]['unit_prices'] == {
        'stock_cost': '100', 'tender_price': '100', 'currency': 'RUB', 'unit': 'кг'}


@pytest.mark.parametrize(('currency', 'reason'), [('USD', 'STOCK_CURRENCY_MISMATCH'), (None, 'STOCK_CURRENCY_UNKNOWN')])
def test_mismatched_or_unknown_currency_is_explained(currency, reason):
    result = present(card_case(currency=currency))
    line = result['analysis']['positions'][0]
    assert line['unit_prices']['stock_cost'] is None
    assert line['unit_prices']['tender_price'] == '10'
    assert reason in line['unit_price_reasons']


def test_tender_price_conflict_is_not_hidden_by_unit_price():
    data = card_case()
    data[0]['tender']['positions'][0]['position_price'] = '200'
    line = present(data)['analysis']['positions'][0]
    assert line['unit_prices']['tender_price'] is None
    assert 'TENDER_PRICES_CONFLICT' in line['unit_price_reasons']


def test_missing_conversion_does_not_compare_kilograms_to_pieces():
    line = present(card_case(unit='кг'))['analysis']['positions'][0]
    assert line['unit_prices']['stock_cost'] is None
    assert 'PRICE_UNIT_CONVERSION_UNKNOWN' in line['unit_price_reasons']


def test_original_evaluation_time_and_rule_version_drive_fulfillment():
    data = card_case(free='10')
    data[0]['fulfillment']['version'] = 3
    data[0]['tender']['terms'] = {'start_at': '2026-11-02T00:00:00+00:00',
                                'delivery_deadline': '2026-11-05T00:00:00+00:00',
                                'delivery_days': 0, 'logistics_cost': '0', 'other_cost': '0',
                                'tax_comparable': True}
    result = present(data)
    assert result['fulfillment']['version'] == 3
    assert 'DELIVERY_DEADLINE_PASSED' not in result['fulfillment']['reasons']
    assert result['opportunity']['score'] == 100


def test_missing_original_time_never_uses_todays_date():
    data = card_case()
    del data[0]['evaluated_at']
    result = present(data)
    assert result['fulfillment']['version'] == data[0]['fulfillment']['version']
    assert result['fulfillment']['status'] == 'needs_review'
    assert result['fulfillment']['full_economics']['scope'] is None
    assert 'EVALUATION_TIME_UNKNOWN' in result['projection']['reasons']
    assert 'EVALUATION_TIME_UNKNOWN' in result['opportunity']['reasons']


def test_current_review_is_not_marked_stale():
    data = card_case()
    data[0]['review']['prompt_version'] = 'card-review-v5'
    assert present(data)['projection']['review_stale'] is False


def test_zero_stock_with_conflicting_catalog_prices_has_no_single_stock_price():
    data = card_case(free='0')
    data[2]['rows'].append(row('B12-OTHER', 'Болт М12', free='0', cost='7'))
    matching = match_positions(data[0]['tender'], data[2], SelectingModel(), 'test', lambda: None)
    data[0]['analysis'] = calculate_analysis(data[0]['tender'], data[2], matching)
    line = present(data)['analysis']['positions'][0]
    assert line['unit_prices']['stock_cost'] is None
    assert 'STOCK_PRICES_CONFLICT' in line['unit_price_reasons']


def test_weighted_stock_price_uses_allocated_skus_and_decimal_precision():
    data = card_case(free='2', cost='0.123456789123456789')
    data[2]['rows'].append(row('B12-OTHER', 'Болт М12', free='2', cost='0.323456789123456789'))
    matching = match_positions(data[0]['tender'], data[2], SelectingModel(), 'test', lambda: None)
    data[0]['analysis'] = calculate_analysis(data[0]['tender'], data[2], matching)
    line = present(data)['analysis']['positions'][0]
    assert line['unit_prices']['stock_cost'] == '0.223456789123456789'


def test_unknown_tender_currency_does_not_borrow_stock_currency_for_tender_price():
    data = card_case()
    data[0]['tender']['positions'][0]['currency'] = None
    line = present(data)['analysis']['positions'][0]
    assert line['unit_prices'] == {'stock_cost': '5', 'tender_price': None, 'currency': 'RUB', 'unit': 'шт'}
    assert 'TENDER_CURRENCY_UNKNOWN' in line['unit_price_reasons']


def test_known_full_profit_with_unknown_schedule_is_not_described_as_partial_economics():
    data = card_case(free='10')
    data[0]['tender']['terms'] = {'logistics_cost': '0', 'other_cost': '0', 'tax_comparable': True}
    result = present(data)
    assert result['opportunity']['basis'] == 'full'
    text = ' '.join(result['report_summary']['paragraphs'])
    assert 'Полный расчёт заказа' in text
    assert 'части заказа' not in result['report_summary']['paragraphs'][-1]


def test_price_projection_alone_does_not_need_profile_or_mutate_analysis():
    data = card_case()
    original = deepcopy(data[0]['analysis'])
    module = importlib.import_module('bervel.modules.ai_tender_radar.presentation')
    result = module.with_unit_prices(data[0]['tender'], data[2], data[0]['analysis'])
    assert data[0]['analysis'] == original
    assert result['positions'][0]['unit_prices']['stock_cost'] == '5'
    assert result['positions'][0]['unit_prices']['tender_price'] == '10'


def test_historical_profile_supplies_production_cost_and_schedule():
    from test_ai_radar_production import case

    card, _, warehouse = card_case()
    template_tender, _, _, profile = case()
    profile['products'][0]['sku'] = 'B12'
    card['tender']['terms'] = template_tender['terms']
    original_profile = deepcopy(profile)
    result = present((card, profile, warehouse))
    assert profile == original_profile
    assert result['fulfillment']['status'] == 'feasible'
    assert result['fulfillment']['production_allocations'][0]['quantity'] == '6'
    assert result['fulfillment']['full_economics']['profit'] == '50'
    assert result['opportunity']['score'] == 100
    assert result['report_summary']['decision'] == 'participate'


def test_report_formats_long_money_and_margin_without_rounding_calculation():
    data = card_case(free='1000', quantity=3000, price=10, cost='3.3333333333333333333333333333333333333333')
    result = present(data)
    money = result['report_summary']['paragraphs'][2]
    assert 'выручка 10 000' in money
    assert 'затраты 3 333,33' in money
    assert 'прибыль 6 666,67 RUB' in money
    assert 'маржа 66,67%' in money
    assert '33,33% стоимости заказа' in money
    assert result['analysis']['positions'][0]['unit_prices']['stock_cost'] == data[2]['rows'][0]['unit_cost']


def test_decline_report_names_capacity_blocker_and_position():
    from test_ai_radar_production import case

    card, _, warehouse = card_case(free='0', quantity=100)
    template, _, _, profile = case(days=1, rate='20')
    card['tender']['terms'] = template['terms']
    profile['products'][0]['sku'] = 'B12'
    result = present((card, profile, warehouse))
    assert result['report_summary']['decision'] == 'decline'
    conclusion = result['report_summary']['paragraphs'][-1].casefold()
    assert 'болт м12' in conclusion
    assert 'мощност' in conclusion
    assert 'срок' in conclusion
    assert 'или отсутствует прибыль' not in conclusion


def test_production_report_uses_readable_date_and_tender_shortage_unit():
    from test_ai_radar_production import case

    card, _, warehouse = card_case(free='0', unit='кг', quantity=2, price=200, weight_per_piece_kg='0.05')
    template, _, _, profile = case()
    card['tender']['terms'] = template['terms']
    profile['products'][0].update(sku='B12', attributes={'weight_per_piece_kg': '0.05'})
    result = present((card, profile, warehouse))
    manufacturing = result['report_summary']['paragraphs'][1]
    assert '2 кг' in manufacturing
    assert '04.11.2026' in manufacturing
    assert 'T00:' not in manufacturing
    assert 'ед.' not in manufacturing


def test_report_prioritizes_largest_comparable_shortage_and_keeps_names():
    data = card_case(free='4')
    for index, quantity in enumerate((100, 200, 300), start=2):
        position = deepcopy(data[0]['tender']['positions'][0])
        diameter = 8 + index * 4
        position.update(id=f'1:0:{index}', quantity=str(quantity), name=f'Болт М{diameter}')
        data[0]['tender']['positions'].append(position)
        data[2]['rows'].append(row(f'B{diameter}', f'Болт М{diameter}', free='0'))
    matching = match_positions(data[0]['tender'], data[2], SelectingModel(), 'test', lambda: None)
    data[0]['analysis'] = calculate_analysis(data[0]['tender'], data[2], matching)
    summary = present(data)['report_summary']['paragraphs'][0]
    assert summary.index('Болт М24') < summary.index('Болт М20') < summary.index('Болт М16')


def test_missing_original_time_cannot_reuse_old_feasibility_after_matches_are_rejected():
    data = card_case()
    del data[0]['evaluated_at']
    data[0]['analysis']['positions'][0]['matches'] = []
    data[0]['fulfillment'].update(status='feasible', full_economics={
        'scope': 'full', 'currency': 'RUB', 'revenue': '100', 'cost': '50'})
    original = deepcopy(data)
    result = present(data)
    assert data == original
    assert result['analysis']['positions'][0]['stock_status'] == 'unconfirmed'
    assert result['opportunity']['score'] == 0
    assert result['opportunity']['basis'] == 'unknown'
    assert result['opportunity']['data_status'] == 'needs_review'
    assert result['report_summary']['decision'] == 'review'


def test_unavailable_original_stock_preserves_history_and_invalidates_current_claims():
    card, _, _ = card_case()
    card['opportunity'] = {'score': 100, 'basis': 'full', 'known_share': '1', 'margin_percent': '50'}
    card['review'].update(prompt_version='card-review-v5', state='complete')
    card['report_summary'] = {'decision': 'participate', 'paragraphs': ['Старое решение']}
    original = deepcopy(card)
    module = importlib.import_module('bervel.modules.ai_tender_radar.presentation')
    result = module.present_unavailable(card, 'ORIGINAL_STOCK_UNAVAILABLE')
    assert card == original
    for key in ('analysis', 'assessment', 'fulfillment', 'evaluated_at', 'tender', 'review'):
        assert result[key] == original[key]
    assert result['opportunity']['score'] == 0
    assert result['opportunity']['basis'] == 'unknown'
    assert result['opportunity']['known_share'] is None
    assert result['opportunity']['margin_percent'] is None
    assert result['opportunity']['data_status'] == 'needs_review'
    assert result['opportunity']['reasons'] == ['ORIGINAL_STOCK_UNAVAILABLE']
    assert result['projection']['review_stale'] is True
    assert result['projection']['reasons'] == ['ORIGINAL_STOCK_UNAVAILABLE']
    assert result['report_summary']['decision'] == 'review'
    assert 'недоступен' in ' '.join(result['report_summary']['paragraphs'])
    assert 'unit_prices' not in result['analysis']['positions'][0]
    result['analysis']['positions'][0]['shortage'] = '0'
    assert card == original


def test_unavailable_projection_accepts_card_without_finished_analysis():
    module = importlib.import_module('bervel.modules.ai_tender_radar.presentation')
    card = {'analysis': None, 'review': None, 'assessment': None}
    result = module.present_unavailable(card, 'ORIGINAL_STOCK_UNAVAILABLE')
    assert result['analysis'] is None
    assert result['opportunity']['score'] == 0
    assert result['projection']['review_stale'] is True
    assert result['report_summary']['decision'] == 'review'
