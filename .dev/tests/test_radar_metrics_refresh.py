"""Latest numerical metrics reuse saved proof without model/source side effects."""
from copy import deepcopy
from datetime import datetime, timezone
import importlib
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_radar_presentation import card_case


NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def implementation():
    name = 'bervel.modules.ai_tender_radar.metrics_refresh'
    assert importlib.util.find_spec(name), 'Local metrics use case is missing'
    return importlib.import_module(name)


def metrics_case(**changes):
    card, _, stock = card_case(**changes)
    card.update(analysis_id='analysis-one', candidate_id='candidate-one', details_loaded=True,
        analysis_settings_snapshot_id='frozen-one')
    profile = {'stock_artifact_id': 'stock-one', 'stock_sha256': 'a' * 64,
        'products': [{'id': 'p', 'sku': r['sku'], 'name': r['product'], 'unit': r['unit'],
            'attributes': deepcopy(r['attributes']), 'enabled': True} for r in stock['rows']],
        'production': [], 'resources': [], 'experimental_products': [], 'include_experimental': False,
        'scoring_policy': {}}
    card['tender'].setdefault('terms', {}).update(logistics_cost='0', other_cost='0', tax_comparable=True)
    return card, profile, stock


def calculate(data):
    return implementation().calculate_metrics_projection(*data, snapshot_id='latest-one', now=NOW)


def with_origin(data):
    card, profile, stock = data
    card['_metrics_origin'] = implementation().metrics_dependencies(profile, stock)
    return data


def test_quantity_and_cost_recalculate_without_changing_frozen_analysis():
    data = with_origin(metrics_case())
    data[2]['rows'][0].update(free_quantity='8', unit_cost='6')
    original = deepcopy(data)
    result = calculate(data)
    assert data == original
    assert result['coverage'] == {'percent': '80', 'incomplete': False}
    assert result['margin']['percent'] == '40'
    assert result['margin']['scope'] == 'confirmed'
    assert result['lines'][0]['covered'] == '8'
    assert result['lines'][0]['shortage'] == '2'
    assert result['lines'][0]['unit_prices']['stock_cost'] == '6'
    assert result['dependencies_changed'] is True


def test_full_stock_margin_is_full_and_no_scheduler_is_called(monkeypatch):
    data = with_origin(metrics_case(free='10', cost='5'))
    monkeypatch.setattr('bervel.modules.ai_tender_radar.production._capacity',
        lambda *a, **k: pytest.fail('Metrics must not build a production calendar'))
    result = calculate(data)
    assert result['coverage']['percent'] == '100'
    assert result['margin']['percent'] == '50'
    assert result['margin']['scope'] == 'full'
    assert result['dependencies_changed'] is False


@pytest.mark.parametrize('change', ['absent', 'changed_attribute'])
def test_missing_or_changed_technical_sku_cannot_keep_confirmed_latest_coverage(change):
    data = with_origin(metrics_case())
    if change == 'absent':
        data[2]['rows'] = []
    else:
        data[2]['rows'][0]['attributes']['thread_diameter_mm'] = '16'
    result = calculate(data)
    assert result['coverage']['incomplete'] is True
    assert result['margin']['scope'] == 'unknown'
    assert result['lines'][0]['stock_status'] == 'unconfirmed'


def test_new_alternative_is_not_selected_without_model_matching():
    data = with_origin(metrics_case())
    data[2]['rows'].append(dict(data[2]['rows'][0], sku='NEW', free_quantity='100'))
    result = calculate(data)
    assert result['lines'][0]['covered'] == '4'
    assert {a['sku'] for a in result['lines'][0]['allocations']} == {'B12'}


def test_changed_catalog_invalidates_old_negative_proof_without_inventing_match():
    data = with_origin(metrics_case())
    line = data[0]['analysis']['positions'][0]
    line.update(matches=[], allocations=[], covered='0', shortage='10',
        stock_status='none', stock_reason='NO_COMPATIBLE_SKU')
    data[2]['rows'].append(dict(data[2]['rows'][0], sku='NEW', free_quantity='100'))
    result = calculate(data)
    assert result['coverage']['incomplete'] is True
    assert result['lines'][0]['stock_status'] == 'unconfirmed'
    assert result['lines'][0]['allocations'] == []


def test_text_only_production_labels_do_not_invalidate_score_dependencies():
    data = metrics_case()
    data[1]['resources'] = [{'group_id': 'old', 'calendar': [0,1,2,3,4], 'default_daily_capacity': '1'}]
    data[1]['production'] = [{'product_id': 'p', 'resource_group_id': 'old', 'units_per_workday': '20',
        'production_unit_cost': '2', 'setup_workdays': '0', 'material_ready_at': NOW.isoformat(),
        'currency': 'RUB', 'evidence': 'old label'}]
    with_origin(data)
    data[1]['resources'][0]['group_id'] = 'new'
    data[1]['production'][0].update(resource_group_id='new', evidence='new label')
    assert calculate(data)['dependencies_changed'] is False


def test_primary_card_has_no_invented_metrics():
    data = metrics_case()
    data[0]['analysis'] = None
    result = calculate(data)
    assert result['coverage']['percent'] is None
    assert result['margin']['percent'] is None
    assert result['lines'] == []


def test_latest_read_overlay_updates_numeric_details_and_preserves_frozen_objects():
    module = implementation()
    data = with_origin(metrics_case())
    data[2]['rows'][0]['free_quantity'] = '8'
    projection = calculate(data)
    card = data[0]
    card['metrics_projection'] = projection
    from bervel.modules.ai_tender_radar.individual_rating import build_card_rating_input, PROMPT_VERSION
    snapshot = build_card_rating_input(card, data[1], data[2])
    card['individual_rating'] = {'status': 'complete', 'score': 0, 'input': snapshot,
        'fingerprint': snapshot['fingerprint'], 'prompt_version': PROMPT_VERSION}
    original = deepcopy(card)
    public = module.project_metrics(card)
    assert card == original
    assert public['analysis']['positions'][0]['covered'] == '8'
    assert public['metrics']['stock_coverage_percent'] == '80'
    assert public['opportunity']['margin_percent'] == '50'
    assert public['projection']['metrics_review_stale'] is True
    assert public['rating']['previous_score'] == 0
    assert 'lines' not in public['metrics_projection']


@pytest.mark.parametrize('change', ['experimental_cost', 'official_unit'])
def test_selected_production_change_persists_new_metrics_and_stales_old_score(change):
    module = implementation()
    card, profile, stock = metrics_case(free='4' if change == 'official_unit' else '0')
    if change == 'experimental_cost':
        old_sku = stock['rows'][0]['sku']
        stock['rows'][0]['sku'] = 'EXP:p'
        for line in card['analysis']['positions']:
            for key in ('matches', 'allocations'):
                for value in line.get(key, []):
                    if value.get('sku') == old_sku:
                        value['sku'] = 'EXP:p'
        profile['experimental_products'] = profile['products']
        profile['products'] = []
        profile['include_experimental'] = True
    profile['resources'] = [{'group_id': 'g', 'calendar': [0, 1, 2, 3, 4], 'default_daily_capacity': '1'}]
    profile['production'] = [{'product_id': 'p', 'resource_group_id': 'g', 'units_per_workday': '20',
        'production_unit_cost': '2', 'currency': 'RUB', 'setup_workdays': '0',
        'material_ready_at': NOW.isoformat(), 'evidence': 'proof'}]
    card['analysis_metrics_dependencies'] = module.metrics_dependencies(profile, stock)
    from bervel.modules.ai_tender_radar.individual_rating import build_card_rating_input, PROMPT_VERSION
    snapshot = build_card_rating_input(card, profile, stock)
    card['individual_rating'] = {'status': 'complete', 'score': 83, 'input': snapshot,
        'fingerprint': snapshot['fingerprint'], 'prompt_version': PROMPT_VERSION}
    frozen = deepcopy({key: card[key] for key in ('analysis', 'individual_rating')})
    writes = []
    def upsert(run_id, candidate_id, patch):
        writes.append(deepcopy(patch))
        card.update(deepcopy(patch))
    pipeline = SimpleNamespace(repository=SimpleNamespace(
        cards=lambda run_id: [{'data': deepcopy(card)}], upsert_card=upsert),
        jobs=SimpleNamespace(heartbeat=lambda job_id: None))
    job = {'job_id': uuid4(), 'operation': {'settings_snapshot_id': 'frozen-one'}}
    run = {'run_id': uuid4()}
    module.refresh_metrics_cards(pipeline, job, run, profile, stock, {}, lambda: None)
    before = deepcopy(card['metrics_projection'])
    if change == 'experimental_cost':
        profile['production'][0]['production_unit_cost'] = '8'
    else:
        profile['products'][0]['unit'] = 'кг'
    module.refresh_metrics_cards(pipeline, job, run, profile, stock, {}, lambda: None)
    assert len(writes) == 2
    assert card['metrics_projection']['input_fingerprint'] != before['input_fingerprint']
    assert card['metrics_projection']['margin'] != before['margin']
    assert card['metrics_projection']['dependencies_changed'] is True
    assert module.project_metrics(card)['rating']['previous_score'] == 83
    assert {key: card[key] for key in frozen} == frozen


@pytest.mark.parametrize('change', ['cost', 'quantity'])
def test_changed_metrics_cannot_confirm_full_cost_while_production_alternatives_are_unresolved(change, monkeypatch):
    data = with_origin(metrics_case())
    card, profile, stock = data
    profile['resources'] = [{'group_id': 'g', 'calendar': [0, 1, 2, 3, 4], 'default_daily_capacity': '1'}]
    route = {'product_id': 'p', 'resource_group_id': 'g', 'units_per_workday': '20',
        'production_unit_cost': '2', 'currency': 'RUB', 'setup_workdays': '0',
        'material_ready_at': NOW.isoformat(), 'evidence': 'proof'}
    profile['production'] = [route, dict(route, production_unit_cost='8')]
    with_origin(data)
    stock['rows'][0]['unit_cost' if change == 'cost' else 'free_quantity'] = '6'
    monkeypatch.setattr('bervel.modules.ai_tender_radar.production._capacity',
        lambda *a, **k: pytest.fail('Metrics must not schedule production alternatives'))
    result = calculate(data)
    assert result['margin']['scope'] == 'confirmed'
    assert result['full_economics']['scope'] is None
    assert any(reason.startswith('ALTERNATIVE_SCHEDULE_UNRESOLVED') for reason in result['margin']['reasons'])


def test_an_unknown_lot_with_one_proved_position_cannot_be_presented_as_zero_coverage():
    data = with_origin(metrics_case())
    card, profile, stock = data
    position = deepcopy(card['tender']['positions'][0])
    position['id'] = 'unconfirmed'
    card['tender']['positions'].append(position)
    line = dict(deepcopy(card['analysis']['positions'][0]), id='unconfirmed', matches=[],
        allocations=[], stock_status='unconfirmed', stock_reason='MODEL_NOT_COMPLETED')
    card['analysis']['positions'].append(line)
    stock['rows'][0]['free_quantity'] = '8'
    assert calculate(data)['coverage'] == {'percent': None, 'incomplete': True}


def test_identical_dependencies_reuse_completed_numeric_stage_without_changing_its_lot_scope():
    data = metrics_case()
    card, profile, stock = data
    card['stock_coverage_by_piece'] = {'percent': None, 'incomplete': True}
    card['fulfillment']['full_economics'] = {'scope': None, 'currency': 'RUB', 'revenue': None,
        'cost': None, 'profit': None, 'margin_percent': None}
    card['analysis_completed_refresh_id'] = 'old-complete'
    with_origin(data)
    result = calculate(data)
    assert result['coverage'] == {'percent': None, 'incomplete': True}
    assert result['stock_economics'] == card['analysis']['economics']
    assert result['full_economics'] == card['fulfillment']['full_economics']


def test_changed_stock_preserves_unknown_coverage_after_partial_model_failure():
    data = with_origin(metrics_case())
    card, profile, stock = data
    card['analysis']['model_error'] = {'code': 'MODEL_TIMEOUT', 'message': 'Partial response'}
    card['stock_coverage_by_piece'] = {'percent': None, 'incomplete': True}
    stock['rows'][0]['free_quantity'] = '8'
    assert calculate(data)['coverage'] == {'percent': None, 'incomplete': True}
