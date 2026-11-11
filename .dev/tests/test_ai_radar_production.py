from copy import deepcopy
from datetime import datetime, timezone

from bervel.modules.ai_tender_radar.production import evaluate_fulfillment


NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def case(*, covered='40', required='100', days=3, rate='20', cost='5'):
    stamp = NOW.isoformat()
    tender = {'positions': [{'id': 'p', 'quantity': required, 'unit': 'шт', 'item_price': '10',
                             'position_price': None, 'currency': 'RUB'}], 'limitations': [],
              'terms': {'start_at': stamp, 'delivery_deadline': f'2026-11-{2 + days:02d}T00:00:00+00:00',
                        'delivery_days': 0, 'logistics_cost': '0', 'other_cost': '0',
                        'tax_comparable': True, 'confirmed_at': stamp, 'evidence': ['document']}}
    stock = {'data_kind': 'reported', 'rows': [{'sku': 's', 'free_quantity': covered, 'unit': 'шт',
             'unit_cost': cost, 'currency': 'RUB', 'as_of': stamp, 'attributes': {}}]}
    analysis = {'positions': [{'id': 'p', 'required': required, 'covered': covered,
                'shortage': str(int(required) - int(covered)), 'matches': [{'sku': 's', 'evidence': ['verified']}],
                'allocations': [] if covered == '0' else [{'sku': 's', 'stock_quantity': covered,
                'position_quantity': covered, 'stock_unit': 'шт', 'stock_unit_cost': cost, 'stock_currency': 'RUB'}],
                'economics': {'scope': 'confirmed'}}]}
    profile = {'products': [{'id': 'product', 'sku': 's', 'name': 'Product', 'unit': 'шт', 'enabled': True,
                           'attributes': {}, 'manufactured_before': True, 'evidence': ['record']}],
               'production': [{'product_id': 'product', 'resource_group_id': 'g', 'units_per_workday': rate,
                               'production_unit_cost': cost, 'currency': 'RUB', 'setup_workdays': '0',
                               'material_ready_at': stamp, 'confirmed_at': stamp, 'evidence': ['record']}],
               'resources': [{'group_id': 'g', 'calendar': [0, 1, 2, 3, 4], 'default_daily_capacity': '1',
                              'free_capacity_by_day': {}, 'confirmed_at': stamp}],
               'scoring_policy': {'max_age_by_source': {'stock': '7', 'production': '7', 'resources': '7', 'costs': '7'}}}
    return tender, stock, analysis, profile


def evaluate(data, *, rule_version=2):
    return evaluate_fulfillment(*data, now=NOW, rule_version=rule_version)


def test_partial_stock_three_workdays_and_stress_replans():
    plan = evaluate(case())
    assert plan['status'] == 'feasible'
    assert plan['production_allocations'][0]['quantity'] == '60'
    assert plan['full_economics']['cost'] == '500'
    assert plan['stock_share'] == '0.4'
    assert plan['utilization'] == '1'
    assert plan['stress']['status'] == 'infeasible'


def test_shared_resources_are_not_spent_twice():
    data = case(covered='0', days=4)
    tender, stock, analysis, profile = data
    second = deepcopy(tender['positions'][0]); second['id'] = 'q'
    tender['positions'].append(second)
    second = deepcopy(analysis['positions'][0]); second['id'] = 'q'
    analysis['positions'].append(second)
    profile['production'][0]['units_per_workday'] = '34'
    assert evaluate(data)['status'] == 'infeasible'


def test_stock_only_needs_no_production_norm():
    data = case(covered='100')
    data[3]['production'] = []
    data[3]['resources'] = []
    plan = evaluate(data)
    assert plan['status'] == 'feasible'
    assert plan['full_economics']['scope'] == 'full'
    assert plan['utilization'] == '0'


def test_legacy_never_manufactured_is_proven_impossible_but_unknown_flag_is_review():
    data = case()
    data[3]['products'][0]['manufactured_before'] = False
    assert evaluate(data,rule_version=1)['status'] == 'infeasible'
    data[3]['products'][0]['manufactured_before'] = None
    assert evaluate(data,rule_version=1)['status'] == 'needs_review'


def test_unknown_calendar_cost_material_tax_and_freshness_stay_unknown():
    for target, key in [('resource', 'calendar'), ('capability', 'material_ready_at'),
                        ('capability', 'production_unit_cost'), ('terms', 'tax_comparable')]:
        data = case()
        mapping = {'resource': data[3]['resources'][0], 'capability': data[3]['production'][0],
                   'terms': data[0]['terms']}[target]
        mapping[key] = None
        assert evaluate(data)['status'] == 'needs_review', key
    data = case()
    data[3]['scoring_policy']['max_age_by_source'] = {}
    assert evaluate(data)['status'] == 'needs_review'


def test_application_deadline_is_not_delivery_deadline():
    data = case()
    data[0]['ApplicationDeadline'] = data[0]['terms'].pop('delivery_deadline')
    assert evaluate(data)['status'] == 'needs_review'


def test_material_release_and_setup_consume_real_calendar():
    data = case(days=4)
    data[3]['production'][0]['setup_workdays'] = '1'
    data[3]['production'][0]['setup_cost'] = '0'
    assert evaluate(data)['status'] == 'feasible'
    data[3]['production'][0]['material_ready_at'] = '2026-11-04T00:00:00+00:00'
    assert evaluate(data)['status'] == 'infeasible'


def test_input_snapshots_are_unchanged():
    data = case()
    original = deepcopy(data)
    evaluate(data)
    assert data == original


def test_unknown_setup_cost_and_stale_stock_cost_are_not_zero():
    data = case()
    data[3]['production'][0]['setup_workdays'] = '0.5'
    assert evaluate(data)['status'] == 'needs_review'
    data = case(covered='100')
    data[1]['rows'][0]['as_of'] = '2026-10-31T00:00:00+00:00'
    data[3]['scoring_policy']['max_age_by_source']['costs'] = '1'
    assert evaluate(data)['status'] == 'needs_review'


def test_calendar_gaps_are_unknown_and_confirmed_zero_is_infeasible():
    data = case()
    resource = data[3]['resources'][0]
    resource.pop('default_daily_capacity')
    assert evaluate(data)['status'] == 'needs_review'
    resource['default_daily_capacity'] = '0'
    assert evaluate(data)['status'] == 'infeasible'


def test_weekends_and_delivery_are_excluded_from_window():
    data = case(covered='0', rate='25', days=7)
    data[0]['terms']['delivery_days'] = 2
    assert evaluate(data)['status'] == 'feasible'
    data[0]['terms']['delivery_days'] = 4
    assert evaluate(data)['status'] == 'infeasible'


def test_piece_rounding_and_material_release_utilization():
    data = case(covered='0', required='3', rate='2', days=4)
    data[0]['positions'][0]['unit'] = 'кг'
    data[3]['products'][0]['attributes']['weight_per_piece_kg'] = '2'
    plan = evaluate(data)
    assert plan['production_allocations'][0]['quantity'] == '2'
    assert plan['full_economics']['cost'] == '10'
    data = case(covered='0', rate='100', days=4)
    data[3]['production'][0]['material_ready_at'] = '2026-11-05T00:00:00+00:00'
    assert evaluate(data)['utilization'] == '1'


def test_alternative_route_failure_is_not_proof_of_impossibility():
    data = case(covered='0', rate='1')
    second = deepcopy(data[3]['production'][0]); second['units_per_workday'] = '100'
    data[3]['production'].append(second)
    assert evaluate(data)['status'] == 'needs_review'


def test_shipping_deadline_prefix_not_only_final_average():
    data = case(covered='0', rate='100', days=4)
    data[0]['positions'][0]['delivery_deadline'] = '2026-11-03T00:00:00+00:00'
    plan = evaluate(data)
    assert plan['status'] == 'feasible'
    assert plan['utilization'] == '1'
    data[3]['production'][0]['units_per_workday'] = '50'
    assert evaluate(data)['status'] == 'infeasible'


def test_shared_stock_and_missing_mandatory_line_are_not_confident():
    data = case(covered='100')
    position = deepcopy(data[0]['positions'][0]); position['id'] = 'q'
    data[0]['positions'].append(position)
    assert evaluate(data)['status'] == 'needs_review'
    line = deepcopy(data[2]['positions'][0]); line['id'] = 'q'
    data[2]['positions'].append(line)
    assert evaluate(data)['status'] == 'needs_review'


def test_schedule_certificate_includes_ready_at():
    plan = evaluate(case())
    assert plan['production_allocations'][0]['ready_at'] == '2026-11-05T00:00:00+00:00'


def test_stale_capacity_does_not_prove_rejection():
    data = case()
    data[3]['resources'][0]['default_daily_capacity'] = '0'
    data[3]['resources'][0]['confirmed_at'] = '2026-10-01T00:00:00+00:00'
    assert evaluate(data)['status'] == 'needs_review'


def test_alternative_on_earlier_job_can_release_shared_resource():
    data = case(covered='0', required='100', days=3, rate='100')
    for pos_id in ['q', 'r', 'z']:
        pos = deepcopy(data[0]['positions'][0]); pos['id'] = pos_id
        line = deepcopy(data[2]['positions'][0]); line['id'] = pos_id
        line['matches'] = [{'sku': 't', 'evidence': ['verified']}]
        data[0]['positions'].append(pos); data[2]['positions'].append(line)
    # Alternatives affect the shared calendar, including jobs without alternatives.
    second = deepcopy(data[3]['production'][0]); second['resource_group_id'] = 'h'
    data[3]['production'].append(second)
    resource = deepcopy(data[3]['resources'][0]); resource['group_id'] = 'h'
    data[3]['resources'].append(resource)
    product = deepcopy(data[3]['products'][0]); product.update(id='fixed', sku='t')
    data[3]['products'].append(product)
    capability = deepcopy(data[3]['production'][0]); capability['product_id'] = 'fixed'
    data[3]['production'].append(capability)
    assert evaluate(data)['status'] == 'needs_review'


def test_legacy_false_manufacturing_flag_without_evidence_is_not_proven():
    data = case()
    data[3]['products'][0]['manufactured_before'] = False
    data[3]['products'][0]['evidence'] = []
    assert evaluate(data,rule_version=1)['status'] == 'needs_review'


def test_production_and_score_acceptance_examples_use_recomputed_stress():
    import json
    from bervel.modules.ai_tender_radar.scoring import score_assessment

    data = case(covered='0', rate='25', days=14, cost='6')
    plan = evaluate(data)
    assert plan['utilization'] == '0.4'
    assert plan['full_economics']['margin_percent'] == '40'
    assessment = score_assessment(data[0], plan)
    assert (assessment['score'], assessment['stress_score']) == (100, 89)
    json.dumps(plan)  # All numbers in the persisted plan are decimal strings.
    data = case(covered='0', required='95', rate='100', days=1, cost='6')
    assessment = score_assessment(data[0], evaluate(data))
    assert (assessment['score'], assessment['stress_score']) == (67, 0)


def test_stock_only_obligatory_line_cannot_ignore_its_expired_deadline():
    from bervel.modules.ai_tender_radar.scoring import score_assessment

    data = case(covered='100')
    data[0]['positions'][0]['delivery_deadline'] = '2026-11-01T00:00:00+00:00'
    plan = evaluate(data)
    assert plan['status'] == 'infeasible'
    assert 'DELIVERY_DEADLINE_PASSED:p' in plan['reasons']
    assert score_assessment(data[0], plan)['score'] == 0


def test_fresh_confirmed_zero_production_rate_is_infeasible():
    from bervel.modules.ai_tender_radar.scoring import score_assessment

    data = case(rate='0')
    plan = evaluate(data)
    assert plan['status'] == 'infeasible'
    assert 'PRODUCTION_RATE_ZERO:p' in plan['reasons']
    assert score_assessment(data[0], plan)['score'] == 0


def test_zero_rate_stale_or_with_positive_alternative_is_not_proven_impossible():
    data = case(rate='0')
    data[3]['production'][0]['confirmed_at'] = '2026-10-01T00:00:00+00:00'
    assert evaluate(data)['status'] == 'needs_review'
    data = case(rate='0')
    alternate = deepcopy(data[3]['production'][0]); alternate['units_per_workday'] = '100'
    data[3]['production'].append(alternate)
    assert evaluate(data)['status'] == 'needs_review'


def test_stock_only_needs_no_positive_production_rate():
    assert evaluate(case(covered='100', rate='0'))['status'] == 'feasible'


def test_successfully_certified_alternative_plan_is_feasible():
    data = case()
    alternate = deepcopy(data[3]['production'][0]); alternate['resource_group_id'] = 'h'
    data[3]['production'].append(alternate)
    resource = deepcopy(data[3]['resources'][0]); resource['group_id'] = 'h'
    data[3]['resources'].append(resource)
    assert evaluate(data)['status'] == 'feasible'


def test_unrelated_alternatives_do_not_hide_unique_impossible_line():
    data = case(covered='0', rate='20')
    position = deepcopy(data[0]['positions'][0]); position.update(id='q', quantity='10')
    data[0]['positions'].append(position)
    line = deepcopy(data[2]['positions'][0]); line.update(id='q', required='10', shortage='10', matches=[{'sku': 't', 'evidence': ['verified']}])
    data[2]['positions'].append(line)
    product = deepcopy(data[3]['products'][0]); product.update(id='other', sku='t')
    data[3]['products'].append(product)
    for group in ['h', 'i']:
        capability = deepcopy(data[3]['production'][0]); capability.update(product_id='other', resource_group_id=group, units_per_workday='10')
        data[3]['production'].append(capability)
        resource = deepcopy(data[3]['resources'][0]); resource['group_id'] = group
        data[3]['resources'].append(resource)
    plan = evaluate(data)
    assert plan['status'] == 'infeasible'
    assert 'CAPACITY_INSUFFICIENT:p' in plan['reasons']


def test_legacy_enabled_linked_experimental_product_uses_normal_confirmed_capability():
    data = case()
    data[3]['products'][0]['enabled'] = False
    data[3]['include_experimental'] = True
    data[3]['experimental_products'] = [{'id': 'experiment', 'product_id': 'product', 'enabled': True}]
    assert evaluate(data,rule_version=1)['status'] == 'feasible'
    data[3]['include_experimental'] = False
    assert evaluate(data,rule_version=1)['status'] == 'needs_review'
    data[3]['include_experimental'] = True
    data[3]['experimental_products'][0]['product_id'] = None
    assert evaluate(data,rule_version=1)['status'] == 'needs_review'


def test_explicit_expense_currency_mismatch_keeps_full_cost_unknown():
    for expense, expense_currency in [('logistics_cost', 'logistics_currency'), ('other_cost', 'other_cost_currency')]:
        data = case()
        data[0]['positions'][0]['currency'] = 'EUR'
        data[1]['rows'][0]['currency'] = 'EUR'
        data[2]['positions'][0]['allocations'][0]['stock_currency'] = 'EUR'
        data[3]['production'][0]['currency'] = 'EUR'
        data[0]['terms'].update({expense: '100', expense_currency: 'RUB'})
        plan = evaluate(data)
        assert plan['status'] == 'needs_review'
        assert 'COST_CURRENCY_MISMATCH' in plan['reasons']
        assert plan['full_economics']['scope'] is None
        assert plan['full_economics']['cost'] is None
        assert data[0]['terms'][expense] == '100'
        data[0]['terms'][expense_currency] = 'EUR'
        plan = evaluate(data)
        assert plan['status'] == 'feasible'
        assert plan['full_economics']['cost'] == '600'


def test_same_sku_stock_can_be_moved_to_early_line_while_late_line_is_produced():
    data = case(covered='100', rate='100', days=4)
    tender, stock, analysis, profile = data
    tender['positions'][0]['delivery_deadline'] = '2026-11-06T00:00:00+00:00'
    early = deepcopy(tender['positions'][0]); early.update(id='q', delivery_deadline='2026-11-03T00:00:00+00:00')
    tender['positions'].append(early)
    shortage = deepcopy(analysis['positions'][0]); shortage.update(id='q', covered='0', shortage='100', allocations=[])
    analysis['positions'].append(shortage)
    profile['production'][0]['material_ready_at'] = '2026-11-03T00:00:00+00:00'
    plan = evaluate(data)
    assert plan['status'] == 'needs_review'
    assert 'ALTERNATIVE_STOCK_ALLOCATION_UNRESOLVED:q' in plan['reasons']


def test_shared_sku_stock_is_not_counted_twice_when_replacement_is_impossible():
    for manufactured, rate in [(False, '100'), (True, '0')]:
        data = case(covered='100', rate=rate)
        tender, stock, analysis, profile = data
        early = deepcopy(tender['positions'][0]); early['id'] = 'q'
        tender['positions'].append(early)
        shortage = deepcopy(analysis['positions'][0]); shortage.update(id='q', covered='0', shortage='100', allocations=[])
        analysis['positions'].append(shortage)
        profile['products'][0]['manufactured_before'] = manufactured
        assert evaluate(data,rule_version=1 if manufactured is False else 2)['status'] == 'infeasible'
