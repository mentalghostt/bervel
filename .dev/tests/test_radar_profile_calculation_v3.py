from copy import deepcopy

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.audit import check_invariants
from bervel.modules.ai_tender_radar.production import evaluate_fulfillment
from bervel.modules.ai_tender_radar.scoring import score_assessment
from bervel.modules.company_settings.models import stock_for_profile, validate_profile
from test_ai_radar_production import NOW, case
from test_company_settings import profile


def test_new_calculation_uses_only_profile_facts_and_one_plan():
    tender, stock, analysis, settings = case()
    tender['terms'].pop('confirmed_at')
    stock['rows'][0]['as_of'] = '2000-01-01T00:00:00+00:00'
    settings['production'][0].pop('confirmed_at')
    settings['resources'][0].pop('confirmed_at')
    settings['resources'][0]['free_capacity_by_day'] = {NOW.date().isoformat(): '0'}
    settings['scoring_policy'] = {'max_age_by_source': {'stock': '0'}, 'stress_rate_factor': '0.01'}

    current = evaluate_fulfillment(tender, stock, analysis, settings, now=NOW, rule_version=3)
    assessment = score_assessment(tender, current, settings['scoring_policy'])
    assert current['version'] == 3
    assert current['status'] == 'feasible'
    assert current['full_economics']['profit'] == '500'
    assert current['schedule'][0]['date'] == NOW.date().isoformat()
    assert 'stress' not in current
    assert assessment['version'] == 2 and assessment['score'] == 76
    assert {'stress', 'stress_score'}.isdisjoint(assessment)
    assert {'stress_rate_factor', 'max_age_by_source'}.isdisjoint(assessment['policy'])
    assert check_invariants({'analysis': analysis, 'fulfillment': current, 'assessment': assessment}, stock, settings) == []

    legacy = evaluate_fulfillment(tender, stock, analysis, settings, now=NOW, rule_version=2)
    assert legacy['status'] == 'needs_review'
    assert 'stress' in legacy


def test_experimental_capability_requires_both_switches_and_an_active_resource():
    tender, stock, analysis, settings = case(covered='0', rate='100')
    settings.update(include_experimental=True, experimental_products=[{
        'id': 'experiment', 'sku': 's', 'name': 'Experimental', 'unit': 'шт', 'enabled': True, 'attributes': {}}])
    settings['products'][0]['enabled'] = False
    settings['production'][0]['product_id'] = 'experiment'
    analysis['positions'][0]['matches'][0]['sku'] = 'EXP:experiment'
    projected = stock_for_profile(stock, settings)
    assert [(row['sku'], row['free_quantity']) for row in projected['rows']] == [('EXP:experiment', '0')]
    assert projected['rows'][0]['attributes']['sku'] == 's'
    current = evaluate_fulfillment(tender, projected, analysis, settings, now=NOW)
    assert current['status'] == 'feasible'
    assert current['production_allocations'][0]['sku'] == 'EXP:experiment'
    assert check_invariants({'analysis': analysis, 'fulfillment': current}, projected, settings) == []
    conflicting = deepcopy(settings)
    conflicting['resources'][0]['enabled'] = False
    findings = check_invariants({'analysis': analysis, 'fulfillment': current}, projected, conflicting)
    assert any(finding['reason'] == 'RESOURCE_OVERALLOCATED' for finding in findings)
    for changed in ('include_experimental', 'experimental_product', 'resource'):
        disabled = deepcopy(settings)
        if changed == 'include_experimental':
            disabled['include_experimental'] = False
        elif changed == 'experimental_product':
            disabled['experimental_products'][0]['enabled'] = False
        else:
            disabled['resources'][0]['enabled'] = False
        result = evaluate_fulfillment(tender, projected, analysis, disabled, now=NOW)
        assert not result['production_allocations']
        assert result['status'] != 'feasible'


def test_disabled_first_resource_leaves_an_enabled_route_available():
    tender, stock, analysis, settings = case()
    settings['resources'][0]['enabled'] = False
    settings['resources'].append(dict(settings['resources'][0], group_id='enabled', enabled=True))
    settings['production'].append(dict(settings['production'][0], resource_group_id='enabled'))
    plan = evaluate_fulfillment(tender, stock, analysis, settings, now=NOW)
    assert plan['status'] == 'feasible'
    assert {block['resource_group_id'] for block in plan['schedule']} == {'enabled'}


def test_profile_accepts_experimental_routes_without_confirmation_dates():
    settings = profile()
    settings['experimental_products'] = [{
        'id': 'experimental', 'sku': 'S1', 'name': 'Эксперимент', 'unit': 'шт',
        'attributes': {'material': 'Алюминий'}, 'enabled': True}]
    settings['resources'] = [{'group_id': 'g', 'calendar': [0], 'default_daily_capacity': '0.8'}]
    settings['production'] = [{
        'product_id': 'experimental', 'resource_group_id': 'g', 'units_per_workday': '100',
        'production_unit_cost': '2', 'currency': 'RUB', 'setup_workdays': '0',
        'material_ready_at': NOW.isoformat(), 'evidence': 'Factory data'}]
    assert validate_profile(settings) == settings
    settings['resources'][0]['enabled'] = 'false'
    with pytest.raises(CoreError, match='enabled'):
        validate_profile(settings)
