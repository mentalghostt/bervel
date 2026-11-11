import pytest

from bervel.modules.ai_tender_radar.production import evaluate_fulfillment
from bervel.modules.ai_tender_radar.scoring import score_assessment
from test_ai_radar_production import NOW, case


def test_fully_stocked_order_with_known_costs_does_not_need_a_confirmation_stamp():
    data = case(covered='100')
    del data[0]['terms']['confirmed_at']

    previous = evaluate_fulfillment(*data, now=NOW, rule_version=2)
    assert previous['reasons'] == ['COSTS_FRESHNESS_UNCONFIRMED']
    assert score_assessment(data[0], previous)['score'] is None

    current = evaluate_fulfillment(*data, now=NOW)
    assert current['full_economics']['profit'] == '500'
    assert current['full_economics']['margin_percent'] == '50'
    assert score_assessment(data[0], current)['score'] == 100


@pytest.mark.parametrize(('cost', 'profit', 'margin', 'score'), [
    ('5', '500', '50', None),
    ('10', '0', '0', 0),
    ('12', '-200', '-20', 0),
])
def test_missing_schedule_does_not_erase_proven_margin(cost, profit, margin, score):
    data = case(covered='100', cost=cost)
    del data[0]['terms']['delivery_deadline']

    current = evaluate_fulfillment(*data, now=NOW)
    assert current['version'] == 4
    assert current['status'] == 'needs_review'
    assert current['full_economics']['scope'] == 'full'
    assert current['full_economics']['profit'] == profit
    assert current['full_economics']['margin_percent'] == margin
    assert score_assessment(data[0], current)['score'] == score

    previous = evaluate_fulfillment(*data, now=NOW, rule_version=3)
    assert previous['full_economics']['margin_percent'] is None


@pytest.mark.parametrize(('section', 'missing', 'reason'), [
    ('production', 'material_ready_at', 'MATERIAL_READY_UNKNOWN:p'),
    ('production', 'units_per_workday', 'PRODUCTION_RATE_UNKNOWN:p'),
    ('resources', 'default_daily_capacity', 'CAPACITY_UNKNOWN:p'),
])
def test_confirmed_production_cost_survives_unknown_schedule(section, missing, reason):
    data = case()
    del data[3][section][0][missing]
    current = evaluate_fulfillment(*data, now=NOW)
    assert current['status'] == 'needs_review'
    assert reason in current['reasons']
    assert current['full_economics']['profit'] == '500'
    assert score_assessment(data[0], current)['score'] is None


def test_known_schedule_and_stock_do_not_replace_missing_logistics_cost_with_zero():
    data = case(covered='100')
    data[0]['terms']['logistics_cost'] = None
    current = evaluate_fulfillment(*data, now=NOW)
    assert current['full_economics']['profit'] is None
    assert score_assessment(data[0], current)['score'] is None


def test_missing_cost_does_not_mask_proven_infeasible_production():
    data = case(days=1)
    data[3]['production'][0]['production_unit_cost'] = None
    current = evaluate_fulfillment(*data, now=NOW)
    assert current['status'] == 'infeasible'
    assert 'CAPACITY_INSUFFICIENT:p' in current['reasons']
    assert current['full_economics']['profit'] is None
    assert score_assessment(data[0], current)['score'] == 0
