from copy import deepcopy

import pytest

from bervel.modules.ai_tender_radar import scoring


def assessed(*, revenue='100', cost='70', scope='full', status='needs_review'):
    economics = {'revenue': revenue, 'cost': cost, 'currency': 'RUB', 'scope': scope}
    return {'tender': {'positions': [{'quantity': '10', 'item_price': '10',
                                     'position_price': None, 'currency': 'RUB'}], 'limitations': []},
            'fulfillment': {'status': status, 'full_economics': economics if scope == 'full' else {},
                            'reasons': ['DELIVERY_DEADLINE_UNKNOWN']},
            'analysis': {'economics': economics}}


def test_unknown_is_numeric_zero_without_invented_margin():
    assert callable(getattr(scoring, 'opportunity_assessment', None))
    value = scoring.opportunity_assessment({})
    assert value['score'] == 0
    assert type(value['score']) is int
    assert value['basis'] == 'unknown'
    assert value['known_share'] is None
    assert value['margin_percent'] is None


@pytest.mark.parametrize(('cost', 'expected'), [('101', 0), ('100', 0), ('98.05', 7), ('85', 50), ('70', 100), ('0', 100)])
def test_known_margin_is_scored_even_while_schedule_needs_review(cost, expected):
    value = scoring.opportunity_assessment(assessed(cost=cost))
    assert value['score'] == expected
    assert value['basis'] == 'full'
    assert value['known_share'] == '1'
    assert value['data_status'] == 'needs_review'
    assert 'DELIVERY_DEADLINE_UNKNOWN' in value['reasons']


def test_partial_economics_is_weighted_by_order_value_not_stock_percentage():
    card = assessed(revenue='20', cost='10', scope='confirmed')
    card['analysis']['coverage_percent'] = '90'
    original = deepcopy(card)
    value = scoring.opportunity_assessment(card)
    assert value['score'] == 20
    assert value['known_share'] == '0.2'
    assert value['margin_percent'] == '50'
    assert value['basis'] == 'confirmed'
    assert 'FULL_COSTS_UNKNOWN' in value['reasons']
    assert card == original


def test_unknown_full_order_value_does_not_assume_complete_share():
    card = assessed(revenue='20', cost='10', scope='confirmed')
    card['tender']['positions'].append({'quantity': '100', 'item_price': None,
                                       'position_price': None, 'currency': 'RUB'})
    value = scoring.opportunity_assessment(card)
    assert value['score'] == 0
    assert value['known_share'] is None
    assert value['margin_percent'] == '50'
    assert 'ORDER_REVENUE_UNKNOWN' in value['reasons']


@pytest.mark.parametrize('mutation', ['currency', 'cost', 'conflict', 'source'])
def test_uncomparable_partial_economics_never_receives_a_positive_score(mutation):
    card = assessed(revenue='20', cost='10', scope='confirmed')
    if mutation == 'currency':
        card['tender']['positions'][0]['currency'] = 'USD'
    elif mutation == 'cost':
        card['analysis']['economics']['cost'] = None
    elif mutation == 'conflict':
        card['tender']['positions'][0]['position_price'] = '200'
    else:
        card['tender']['limitations'] = [{'code': 'LOT_OBJECTS_CONFLICT', 'lot': 1}]
    assert scoring.opportunity_assessment(card)['score'] == 0


def test_confirmed_infeasibility_overrides_profitable_economics():
    value = scoring.opportunity_assessment(assessed(status='infeasible'))
    assert value['score'] == 0
    assert 'FULFILLMENT_INFEASIBLE' in value['reasons']


def test_margin_monotonicity_and_policy_target():
    values = [scoring.opportunity_assessment(assessed(cost=str(cost)))['score'] for cost in range(100, -1, -1)]
    assert values == sorted(values)
    assert scoring.opportunity_assessment(assessed(cost='85'), {'target_margin': '0.60'})['score'] == 25
    assert scoring.opportunity_assessment(assessed(cost='85'), {'target_margin': '0'})['score'] == 0


def test_full_loss_is_not_replaced_by_profitable_stock_fragment():
    card = assessed(cost='120')
    card['analysis']['economics'] = {'revenue': '20', 'cost': '10', 'scope': 'confirmed', 'currency': 'RUB'}
    assert scoring.opportunity_assessment(card)['score'] == 0
