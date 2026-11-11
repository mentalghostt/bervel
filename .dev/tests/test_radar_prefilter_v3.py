from copy import deepcopy
from decimal import Decimal

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.selection import collect_candidates
from test_ai_tender_radar_analysis import SelectingModel, need, row, stock, tender


def prefilter(warehouse, profile=None):
    from bervel.modules.ai_tender_radar.prefilter import CatalogPrefilter
    return CatalogPrefilter(warehouse, profile or {'products': [], 'experimental_products': []})


@pytest.mark.parametrize(('quantity', 'expected'), [(500, '2'), (167, '5.988023952095809')])
def test_same_title_and_dimensions_with_tiny_stock_is_rejected(quantity, expected):
    result = prefilter(stock(row('B', 'Болт М12', free='10'))).check(tender(need('Болт М12', quantity)))
    assert result['state'] == 'reject'
    assert float(result['upper_coverage_percent']) == pytest.approx(float(expected))


@pytest.mark.parametrize('coverage', [80, 90, 100])
def test_good_tenders_are_never_rejected_at_threshold(coverage):
    result = prefilter(stock(row('B', 'Болт М12', free=str(coverage)))).check(tender(need('Болт М12', 100)))
    assert result['state'] == 'candidate'
    assert Decimal(result['upper_coverage_percent']) >= coverage


def test_shared_stock_is_not_counted_twice_and_not_consumed_between_tenders():
    selector = prefilter(stock(row('B', 'Болт М12', free='10')))
    shared = tender(need('Болт М12', 10), need('Болт М12', 10))
    assert selector.check(shared)['upper_coverage_percent'] == '50'
    assert selector.check(tender(need('Болт М12', 10)))['state'] == 'candidate'
    assert selector.check(tender(need('Болт М12', 10)))['upper_coverage_percent'] == '100'


def test_near_identical_stock_with_wrong_standard_cannot_inflate_supply():
    warehouse = stock(row('RIGHT', 'Болт М12', free='10', standard='ГОСТ 7798-70'),
                      row('OTHER', 'Болт М12', free='10000', standard='ГОСТ 7805-70'))
    result = prefilter(warehouse).check(tender(need('Болт М12 ГОСТ 7798-70', 100)))
    assert result['state'] == 'reject'
    assert result['upper_coverage_percent'] == '10'


def test_missing_standard_cannot_be_promoted_beyond_frozen_matcher_proofs():
    warehouse = stock(row('PARTIAL', 'Болт М12', free='100'))
    result = prefilter(warehouse).check(tender(need('Болт М12 ГОСТ 7798-70', 100)))
    assert result['state'] == 'reject'
    assert result['upper_coverage_percent'] == '0'


@pytest.mark.parametrize('uncertainty', ['quantity', 'stock', 'unit', 'docs', 'limitations', 'requirements'])
def test_uncertain_quantities_units_and_documents_pass_for_actual_analysis(uncertainty):
    warehouse = stock(row('B', 'Болт М12', free='1'))
    demand = tender(need('Болт М12', 100))
    if uncertainty == 'quantity':
        demand['positions'][0]['quantity'] = None
    elif uncertainty == 'stock':
        warehouse['rows'][0]['free_quantity'] = None
    elif uncertainty == 'unit':
        demand['positions'][0]['unit'] = 'кг'
    elif uncertainty == 'docs':
        demand['source_card'] = {'Docs': [{'Name': 'ТЗ'}]}
    elif uncertainty == 'limitations':
        demand['limitations'] = [{'code': 'REQUIREMENTS_INCOMPLETE'}]
    else:
        demand['positions'][0]['name'] = 'Болты'
    result = prefilter(warehouse).check(demand)
    if uncertainty == 'docs':
        # Documents cannot replace this structured inventory, only restrict it.
        assert result['state'] == 'reject'
        assert result['upper_coverage_percent'] == '1'
    else:
        assert result['state'] == 'unknown'
        assert result['upper_coverage_percent'] is None


def test_bound_is_never_below_actual_unchanged_matcher_and_calculator():
    from bervel.modules.ai_tender_radar.matching import match_positions
    from bervel.modules.ai_tender_radar.calculation import calculate_analysis
    warehouse = stock(row('A', 'Болт М12', free='30'), row('B', 'Болт М12', free='70'),
                      row('N', 'Гайка М12', free='20'))
    selector = prefilter(warehouse)
    for demands in [(100, 10), (20, 100), (100, 100), (200, 100)]:
        demand = tender(need('Болт М12', demands[0]), need('Гайка М12', demands[1]))
        matches = match_positions(demand, warehouse, SelectingModel(), 'check', lambda: None)
        actual = calculate_analysis(demand, warehouse, matches)['coverage_percent']
        bound = selector.check(demand)['upper_coverage_percent']
        assert bound is not None and Decimal(bound) >= Decimal(actual)


def test_large_catalog_ranking_keeps_every_candidate_and_does_not_mutate_input():
    profile = {'products': [{'id': str(i), 'sku': str(i), 'enabled': True,
                            'name': f'Болт М{8 + i % 25} ГОСТ 7798-70 исполнение {i}', 'attributes': {}}
                           for i in range(3100)], 'experimental_products': []}
    profile['products'].append({'id': 'N', 'enabled': True, 'name': 'Гайка М12 ГОСТ 5915-70', 'attributes': {}})
    candidates = [{'id': str(i), 'state': 'candidate', 'brief': {
        'OrderName': 'Поставка хозяйственных товаров', 'Snippets': []}} for i in range(220)]
    candidates.append({'id': 'nut', 'state': 'candidate', 'brief': {'OrderName': 'Гайки М12 ГОСТ 5915-70'}})
    before = deepcopy(candidates)
    ranked = prefilter(stock(), profile).rank(candidates)
    assert len(ranked) == 221
    assert ranked[0]['id'] == 'nut'
    assert candidates == before
    assert ranked[-1]['uncertainty'] is True
    assert 'coverage_percent' not in ranked[0]


def test_proven_unit_conversion_preserves_a_tender_with_actual_full_coverage():
    from bervel.modules.ai_tender_radar.matching import match_positions
    from bervel.modules.ai_tender_radar.calculation import calculate_analysis
    warehouse = stock(row('B', 'Болт М12', free='1', unit='кг', weight_per_piece_kg='0.1'))
    demand = tender(need('Болт М12', 10))
    actual = calculate_analysis(demand, warehouse,
        match_positions(demand, warehouse, SelectingModel(), 'units', lambda: None))
    assert actual['coverage_percent'] == '100'
    assert prefilter(warehouse).check(demand)['state'] == 'candidate'


def test_hidden_document_restrictions_cannot_increase_structured_inventory_bound():
    demand = tender(need('Болт М12', 100))
    demand['documents'] = [{'text': 'Обязательное покрытие: #####'}]
    assert prefilter(stock(row('B', 'Болт М12', free='1'))).check(demand)['state'] == 'reject'


def test_disabled_products_and_opted_out_experiments_do_not_supply_rank_evidence():
    profile = {'products': [{'id': 'off', 'enabled': False, 'name': 'Мыло хозяйственное'}],
               'include_experimental': False,
               'experimental_products': [{'id': 'exp', 'enabled': True, 'name': 'Перчатки резиновые'}]}
    ranked = prefilter(stock(), profile).rank([
        {'id': '1', 'brief': {'OrderName': 'Мыло хозяйственное'}, 'state': 'candidate'},
        {'id': '2', 'brief': {'OrderName': 'Перчатки резиновые'}, 'state': 'candidate'}])
    assert all(item['rank_metadata']['product_ids'] == [] for item in ranked)
    assert all(item['uncertainty'] for item in ranked)


def test_bound_is_safe_for_independently_generated_shared_stock_demands():
    from random import Random
    from bervel.modules.ai_tender_radar.matching import match_positions
    from bervel.modules.ai_tender_radar.calculation import calculate_analysis
    rng = Random(31871)
    warehouse = stock(row('B12A', 'Болт М12', free='20'), row('B12B', 'Болт М12', free='30'),
                      row('B16', 'Болт М16', free='80'), row('N12', 'Гайка М12', free='100'))
    selector = prefilter(warehouse)
    for _ in range(50):
        demand = tender(*(need(rng.choice(('Болт М12', 'Болт М16', 'Гайка М12')), rng.randrange(1, 200))
                          for _ in range(rng.randrange(1, 8))))
        actual = calculate_analysis(demand, warehouse,
            match_positions(demand, warehouse, SelectingModel(), 'generated', lambda: None))['coverage_percent']
        result = selector.check(demand)
        assert Decimal(result['upper_coverage_percent']) >= Decimal(actual)
        if Decimal(actual) >= 80:
            assert result['state'] != 'reject'


def test_search_without_application_limit_exhausts_explicit_finite_scope():
    class Source:
        simulated = True
        def search_page(self, phrase, page, start, end):
            assert start == '2026-11-01T00:00:00+00:00'
            assert end == '2026-11-05T00:00:00+00:00'
            return {'PageNumber': page, 'TotalCount': 251, 'Items': [
                {'Id': str(i), 'OrderName': 'Болт'} for i in range(page * 50, min(251, page * 50 + 50))]}
    result = collect_candidates(Source(), ['болт'], search_max=None,
        checkpoint={'search_start': '2026-11-01T00:00:00+00:00', 'search_end': '2026-11-05T00:00:00+00:00'},
        progress=lambda _: None)
    assert len(result['candidates']) == 251
    assert result['search_attempts'] == 6
    assert result['stop_reason'] == 'search_exhausted'


def test_unbounded_search_requires_source_scope_and_still_refuses_live_source():
    class Source:
        simulated = True
    with pytest.raises(CoreError) as error:
        collect_candidates(Source(), ['болт'], search_max=None, checkpoint={}, progress=lambda _: None)
    assert error.value.code == 'SEARCH_SCOPE_REQUIRED'
    Source.simulated = False
    with pytest.raises(CoreError) as error:
        collect_candidates(Source(), ['болт'], search_max=None, checkpoint={}, progress=lambda _: None)
    assert error.value.code == 'SIMULATION_REQUIRED'


def test_v3_queries_group_thousands_of_skus_without_losing_unknown_products():
    from bervel.modules.ai_tender_radar import selection
    grouped = getattr(selection, 'search_queries_v3', None)
    assert grouped is not None, 'New group search must be available separately from legacy v1'
    profile = {'products': [{'id': str(i), 'name': f'Болт М{8 + i % 25} длина {i}',
                            'enabled': True, 'attributes': {}} for i in range(3100)],
               'include_experimental': False, 'experimental_products': []}
    profile['products'].extend([
        {'id': 'n', 'name': 'Гайка М12', 'enabled': True, 'attributes': {}},
        {'id': 'u', 'name': 'Опора специальная ZZX-43', 'enabled': True, 'attributes': {}},
        {'id': 'off', 'name': 'Отключённое изделие', 'enabled': False, 'attributes': {}}])
    assert grouped(profile) == ['болт', 'гайка', 'Опора специальная ZZX-43']


def test_v3_queries_cover_explicit_groups_and_only_included_experiments():
    from bervel.modules.ai_tender_radar import selection
    grouped = getattr(selection, 'search_queries_v3', None)
    assert grouped is not None
    profile = {'products': [
        {'id': 'a', 'name': 'Тяга ZX-10', 'enabled': True, 'attributes': {'group': 'Тяги монтажные'}},
        {'id': 'b', 'name': 'Тяга ZX-12', 'enabled': True, 'attributes': {'group': 'тяги монтажные'}},
        {'id': 'c', 'name': 'Набор', 'enabled': True, 'attributes': {'group': 'Болты и гайки'}}],
        'include_experimental': False, 'experimental_products': [
            {'id': 'e', 'name': 'Заклёпка специальная', 'enabled': True, 'attributes': {}}]}
    assert grouped(profile) == ['Тяги монтажные', 'болт', 'гайка']
    profile['include_experimental'] = True
    assert grouped(profile) == ['Тяги монтажные', 'болт', 'гайка', 'Заклёпка специальная']


def test_configured_api_requires_explicit_opt_in_official_url_and_key_without_network():
    from bervel.modules.ai_tender_radar.infrastructure.kontur import BASE_URL
    class ConfiguredAdapter:
        simulated = False
        base_url = BASE_URL
        key = 'offline-test-key'
        def search_page(self, phrase, page, start, end):
            return {'Items': [], 'PageNumber': page, 'TotalCount': 0}
    adapter = ConfiguredAdapter()
    result = collect_candidates(adapter, ['болт'], search_max=1, checkpoint={}, progress=lambda _: None,
                                allow_configured_api=True)
    assert result['stop_reason'] == 'search_exhausted'
    for changes in [{'base_url': 'https://example.invalid'}, {'key': ''}, {'simulated': True}]:
        invalid = ConfiguredAdapter()
        for field, value in changes.items():
            setattr(invalid, field, value)
        with pytest.raises(CoreError):
            collect_candidates(invalid, ['болт'], search_max=1, checkpoint={}, progress=lambda _: None,
                               allow_configured_api=True)
