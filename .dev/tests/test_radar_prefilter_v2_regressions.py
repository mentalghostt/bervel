"""Independent production-contract regressions; no simulator/corpus imports."""
from decimal import Decimal
import pytest
from bervel.modules.ai_tender_radar.prefilter import CatalogPrefilter
from bervel.modules.ai_tender_radar.matching import match_positions
from bervel.modules.ai_tender_radar.calculation import calculate_analysis
from test_ai_tender_radar_analysis import SelectingModel, need, row, stock, tender


def decision(warehouse, demand):
    return CatalogPrefilter(warehouse, {}).check(demand)


def actual(warehouse, demand):
    return calculate_analysis(demand, warehouse,
        match_positions(demand, warehouse, SelectingModel(), 'independent-units', lambda: None))['coverage_percent']


def test_unproved_near_names_and_irrelevant_units_cannot_inflate_stock_bound():
    warehouse = stock(row('RIGHT', 'Болт М12 ГОСТ 7798-70', free='2'),
                      row('OTHER', 'Болт М12', free='10000', unit='кг'),
                      row('N', 'Гайка М12', free='10000', unit='упаковка'),
                      row('U', 'Полоса стальная', free=None, unit='м'))
    demand = tender(need('Болт М12 ГОСТ 7798-70', 100))
    result = decision(warehouse, demand)
    assert result['state'] == 'reject'
    assert result['upper_coverage_percent'] == '2'


def test_structured_impossibility_survives_unread_or_masked_document_restrictions():
    warehouse = stock(row('B', 'Болт М12', free='2'))
    demand = tender(need('Болт М12', 100))
    demand['source_card'] = {'Docs': [{'Name': 'ТЗ'}]}
    demand['limitations'] = [{'code': 'DOCUMENT_EXTRACTION_UNKNOWN'}]
    assert decision(warehouse, demand)['state'] == 'reject'
    demand['documents'] = [{'text': 'Обязательное покрытие: #####'}]
    assert decision(warehouse, demand)['upper_coverage_percent'] == '2'


@pytest.mark.parametrize(('demand_unit', 'stock_unit', 'free', 'quantity', 'facts', 'expected'), [
    ('шт', 'кг', '1', 10, {'weight_per_piece_kg': '0.1'}, '100'),
    ('шт', 'упаковка', '1', 100, {'pieces_per_package': '2'}, '2'),
    ('упаковка', 'шт', '2', 10, {'pieces_per_package': '10'}, '2'),
    ('комплект', 'комплект', '2', 100, {'pieces_per_set': '4'}, '2'),
    ('кг', 'кг', '0.2', 10, {'weight_per_piece_kg': '0.3'}, '2'),
    ('кг', 'шт', '1', 10, {'weight_per_piece_kg': '0.2'}, '2'),
])
def test_native_units_and_proven_conversions_match_or_exceed_frozen_calculator(
        demand_unit, stock_unit, free, quantity, facts, expected):
    warehouse = stock(row('B', 'Болт М12', free=free, unit=stock_unit, **facts))
    demand = tender(need('Болт М12', quantity, demand_unit))
    result = decision(warehouse, demand)
    assert result['upper_coverage_percent'] is not None
    assert Decimal(result['upper_coverage_percent']) == pytest.approx(Decimal(expected))
    covered = actual(warehouse, demand)
    assert covered is not None
    assert Decimal(result['upper_coverage_percent']) >= Decimal(covered)
    assert result['state'] == ('candidate' if Decimal(expected) >= 80 else 'reject')


def test_mixed_native_scales_weight_the_denominator_without_reserving_stock():
    warehouse = stock(row('B', 'Болт М12', free='1', unit='упаковка', pieces_per_package='10'),
                      row('N', 'Гайка М12', free='90', unit='шт'))
    demand = tender(need('Болт М12', 1, 'упаковка'), need('Гайка М12', 90))
    assert actual(warehouse, demand) == '100'
    assert decision(warehouse, demand)['upper_coverage_percent'] == '100'
    assert decision(warehouse, demand)['state'] == 'candidate'


@pytest.mark.parametrize('facts', [{}, {'weight_per_piece_kg': '0'}, {'weight_per_piece_kg': 'invalid'}])
def test_real_unknown_conversion_stays_unknown(facts):
    warehouse = stock(row('B', 'Болт М12', free='100', unit='кг', **facts))
    assert decision(warehouse, tender(need('Болт М12', 100)))['state'] == 'unknown'


def test_incompatible_proven_mass_scales_stay_unknown():
    warehouse = stock(row('A', 'Болт М12', free='100', weight_per_piece_kg='0.1'),
                      row('B', 'Болт М12', free='100', weight_per_piece_kg='0.2'))
    assert decision(warehouse, tender(need('Болт М12', 10, 'кг')))['state'] == 'unknown'


def test_missing_inventory_must_not_be_inferred_from_document_or_title():
    demand = tender(need('Болт М12', 100))
    demand['positions'] = []
    demand['source_card'] = {'Docs': [{'Name': 'ТЗ'}]}
    assert decision(stock(row('B', 'Болт М12', free='1000')), demand)['state'] == 'unknown'


def test_model_cannot_add_unproved_or_review_candidate_to_final_matches():
    warehouse = stock(row('NO', 'Болт М12', free='1000'))
    demand = tender(need('Болт М12 ГОСТ 7798-70', 100))
    model = SelectingModel()
    matches = match_positions(demand, warehouse, model, 'missing-standard', lambda: None)
    assert matches.positions[demand['positions'][0]['id']].by_sku == {}
    assert decision(warehouse, demand)['upper_coverage_percent'] == '0'


def test_shared_stock_across_piece_and_mass_demand_has_safe_relaxed_bound():
    warehouse = stock(row('B', 'Болт М12', free='2', weight_per_piece_kg='0.1'))
    demand = tender(need('Болт М12', '0.2', 'кг'), need('Болт М12', 2))
    assert actual(warehouse, demand) == '50'
    # Mass round trips use a deliberately looser independent bound.
    assert Decimal(decision(warehouse, demand)['upper_coverage_percent']) >= Decimal('50')


def test_repeating_mass_conversion_does_not_round_an_exact_eighty_below_threshold():
    warehouse = stock(row('B', 'Болт М12', free='8', unit='кг', weight_per_piece_kg='0.3'))
    demand = tender(need('Болт М12', 10, 'кг'))
    result = decision(warehouse, demand)
    assert result['state'] == 'candidate'
    assert Decimal(result['upper_coverage_percent']) == pytest.approx(Decimal('80'))
    assert Decimal(result['upper_coverage_percent']) >= Decimal(actual(warehouse, demand))


def test_document_inventory_cannot_replace_structured_quantities():
    from copy import deepcopy
    from bervel.core.errors import CoreError
    from bervel.modules.ai_tender_radar.documents import _inventory
    demand = tender(need('Болт М12', 100))
    before = deepcopy(demand['positions'])
    _inventory({'complete': None, 'rows': []}, demand, [])
    assert demand['positions'] == before
    with pytest.raises(CoreError):
        _inventory({'complete': {'value': True}, 'rows': [{'quantity': '1'}]}, demand, [])
    assert demand['positions'] == before


def test_independently_generated_unit_variants_never_understate_frozen_numeric_result():
    from random import Random
    rng = Random(948237)
    confirmed = 0
    for _ in range(300):
        mass = rng.choice(('0.1', '0.3', '0.7'))
        count = rng.choice(('1', '3', '7'))
        facts = {'weight_per_piece_kg': mass, 'pieces_per_set': count, 'pieces_per_package': count}
        warehouse = stock(*(row(str(i), 'Болт М12', free=str(rng.randrange(1, 15)),
                                unit=rng.choice(('шт', 'кг', 'упаковка', 'комплект')), **facts)
                            for i in range(rng.randrange(1, 4))))
        demand = tender(*(need('Болт М12', rng.randrange(1, 15),
                               rng.choice(('шт', 'кг', 'упаковка', 'комплект')))
                          for _ in range(rng.randrange(1, 4))))
        covered = actual(warehouse, demand)
        result = decision(warehouse, demand)
        if covered is not None:
            confirmed += 1
            assert result['upper_coverage_percent'] is not None
            assert Decimal(result['upper_coverage_percent']) >= Decimal(covered)
            if Decimal(covered) >= 80:
                assert result['state'] != 'reject'
    assert confirmed > 100
