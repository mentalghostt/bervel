import json

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.calculation import calculate_analysis, stock_coverage_for_lots
from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender
from bervel.modules.ai_tender_radar.matching import match_positions


def tender(*rows):
    return normalize_tender({'Id': 'abc', 'Currency': 'RUB'},
                            {'Id': 'abc', 'Title': 'Крепёж', 'Lots': [{'JointObjects': list(rows)}]})


def need(name, quantity=10, unit='шт', **fields):
    return {'Name': name, 'Quantity': quantity, 'Okei': unit, **fields}


def row(sku, product, *, free='10', unit='шт', cost='5', currency='RUB', **facts):
    return {'row': 2, 'sku': sku, 'product': product, 'free_quantity': free, 'unit': unit,
            'unit_cost': cost, 'currency': currency, 'as_of': None, 'attributes': facts}


def stock(*rows):
    return {'data_kind': 'reported', 'rows': list(rows)}


class SelectingModel:
    def __init__(self, edit=lambda result: result):
        self.edit = edit
        self.calls = []

    def generate(self, request_id, prompt, *, output_schema, timeout_seconds):
        assert output_schema['additionalProperties'] is False
        assert timeout_seconds <= 180
        assert 'unit_cost' not in prompt and 'PositionPrice' not in prompt
        data = json.loads(prompt.split('\nВХОД\n', 1)[1])
        self.calls.append(data)
        result = {'positions': [{'id': position['id'], 'matches': [
            {'sku': candidate['sku'], 'evidence': [
                {'requirement_id': key, 'field': next(iter(fields))}
                for key, fields in candidate['evidence'].items()]}
            for candidate in position['candidates']]}
            for position in data]}
        return {'answer': json.dumps(self.edit(result)), 'model': 'test'}


def test_cli_selects_only_compatible_skus_and_evidence_is_verified():
    demand = tender(need('Болт М12'), need('Гайка М12'))
    warehouse = stock(row('B12', 'Болт М12', length_mm='95'), row('B16', 'Болт М16'), row('N12', 'Гайка М12'))
    model = SelectingModel()
    result = match_positions(demand, warehouse, model, 'test', lambda: None)
    assert len(model.calls) == 1
    assert result.error is None
    assert set(result.positions['1:0:1'].by_sku) == {'B12'}
    assert set(result.positions['1:0:2'].by_sku) == {'N12'}
    assert not result.positions['1:0:1'].uncertain
    assert result.positions['1:0:1'].by_sku['B12'][0]['stock_value'] == 'Болт М12'


def test_stock_coverage_weights_partial_quantities_and_converts_kg_to_pieces():
    demand = tender(need('Болт М12', 100), need('Гайка М12', 20, 'кг'))
    warehouse = stock(row('B12', 'Болт М12', free='50'),
                      row('N12', 'Гайка М12', free='400', weight_per_piece_kg='0.05'))
    matching = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    analysis = calculate_analysis(demand, warehouse, matching)

    assert stock_coverage_for_lots(demand, warehouse, matching, analysis, {1}) == {
        'percent': '90', 'incomplete': False,
    }


def test_unknown_characteristics_and_options_do_not_prove_fit():
    demand = tender(need('Болт М12', Characteristics=[{'Name': 'Покрытие', 'Values': [{'Value': 'Цинк'}]}]))
    warehouse = stock(row('OPT', 'Болт М12', coating_options='Цинк'), row('NO', 'Болт М12', coating='Фосфат'))
    model = SelectingModel()
    result = match_positions(demand, warehouse, model, 'test', lambda: None)
    assert result.positions['1:0:1'].by_sku == {}
    assert result.positions['1:0:1'].uncertain
    [batch] = model.calls
    [position] = batch
    assert position['candidates'] == []
    assert [candidate['sku'] for candidate in position['review_candidates']] == ['OPT']
    assert 'coating_options' not in json.dumps(position)


def test_model_cannot_promote_a_candidate_with_missing_evidence():
    demand = tender(need('Болт М12', Characteristics=[{'Name': 'Покрытие', 'Values': [{'Value': 'Цинк'}]}]))
    warehouse = stock(row('OPT', 'Болт М12', coating_options='Цинк'))

    def promote(answer):
        answer['positions'][0]['matches'] = [{'sku': 'OPT', 'evidence': []}]
        return answer

    result = match_positions(demand, warehouse, SelectingModel(promote), 'test', lambda: None)
    assert result.error['code'] == 'MODEL_INVALID_OUTPUT'
    assert not result.positions['1:0:1'].by_sku


def test_inline_size_and_standard_use_structured_evidence_and_reject_contradictions():
    name = 'Болт М12×95 ГОСТ 7798-70'
    demand = tender(need(name))
    warehouse = stock(row('OK', 'Болт М12', length_mm='95', standard='ГОСТ 7798-70'),
                      row('WRONG-LENGTH', name, length_mm='90', standard='ГОСТ 7798-70'),
                      row('WRONG-STANDARD', name, length_mm='95', standard='ГОСТ 7805-70'))
    match = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    assert set(match.positions['1:0:1'].by_sku) == {'OK'}


@pytest.mark.parametrize(('name', 'diameter', 'length'), [
    ('Болт М20х80 к.п. 8,8', '20', '80'),
    ('Болт М16х45 к.п.8,8', '16', '45'),
    ('Болт М16×45 к. п. 8.8', '16', '45'),
])
def test_strength_abbreviation_matches_verified_class_without_name_alias(name, diameter, length):
    facts = {'thread_diameter_mm': diameter, 'length_mm': length}
    warehouse = stock(row('OK', 'Болт конструкционный', strength='8.8', **facts),
                      row('WRONG', 'Болт конструкционный', strength='10.9', **facts),
                      row('OPTIONS', 'Болт конструкционный', strength_options='8.8; 10.9', **facts))
    result = match_positions(tender(need(name)), warehouse, SelectingModel(), 'test', lambda: None)
    matches = result.positions['1:0:1'].by_sku
    assert set(matches) == {'OK'}
    assert any(fact['stock_field'] == 'strength' and fact['stock_value'] == '8.8' for fact in matches['OK'])


def test_strength_abbreviation_does_not_remove_other_requirements():
    warehouse = stock(row('PARTIAL', 'Болт конструкционный', thread_diameter_mm='16', length_mm='45',
                          strength='8.8', thread_extent='неполная'))
    demand = tender(need('Болт М16х45 к.п. 8,8 с полной резьбой'))
    result = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    assert not result.positions['1:0:1'].by_sku


def test_plain_washer_size_requires_nominal_diameter_not_hole_diameter():
    demand = tender(need('Шайба 20 ГОСТ 11371-78'))
    warehouse = stock(
        row('OK', 'Шайба; Ø20', standard='ГОСТ 11371-78', nominal_diameter_mm='20', inner_diameter_mm='22'),
        row('WRONG', 'Шайба; Ø18', standard='ГОСТ 11371-78', nominal_diameter_mm='18', inner_diameter_mm='20'),
        row('HOLE-ONLY', 'Шайба', standard='ГОСТ 11371-78', inner_diameter_mm='20'),
    )
    result = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    assert set(result.positions['1:0:1'].by_sku) == {'OK'}


def test_leading_tilde_does_not_hide_nut_or_discard_other_requirements():
    demand = tender(need('~ГАЙКА М16 ГОСТ5915'), need('~ГАЙКА М16 ГОСТ5915 без покрытия'))
    warehouse = stock(row('N16', 'Гайка шестигранная М16', standard='ГОСТ 5915-70', coating='цинк'),
                      row('N20', 'Гайка шестигранная М20', standard='ГОСТ 5915-70'))
    result = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    assert set(result.positions['1:0:1'].by_sku) == {'N16'}
    assert not result.positions['1:0:2'].by_sku


def test_nut_accuracy_and_shape_are_proved_by_stock_fields():
    features = [{'Name': name, 'Values': [{'Value': value, 'Okei': unit}]} for name, value, unit in (
        ('Диаметр резьбы', '12', 'Миллиметр'), ('Класс точности', 'В', None),
        ('Шаг резьбы', '1.75', 'Миллиметр'), ('Форма', 'шестигранная', None), ('Материал', 'Сталь', None),
    )]
    demand = tender(need('Гайка', Characteristics=features))
    facts = {'material': 'сталь', 'thread_pitch_mm': '1.75'}
    warehouse = stock(
        row('OK', 'Гайка М12', product_subtype='шестигранная', accuracy_class='B', **facts),
        row('CLASS-C', 'Гайка М12', product_subtype='шестигранная', accuracy_class='C', **facts),
        row('SQUARE', 'Гайка М12', product_subtype='квадратная', accuracy_class='B', **facts),
        row('NO-SHAPE', 'Гайка М12', accuracy_class='B', **facts),
    )
    result = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    [proof] = result.positions['1:0:1'].by_sku.values()
    assert set(result.positions['1:0:1'].by_sku) == {'OK'}
    assert {'product_subtype', 'accuracy_class'} <= {item['stock_field'] for item in proof}


@pytest.mark.parametrize('prefix', ['evidence.', ''])
def test_cli_field_path_resolves_to_the_same_verified_stock_field(prefix):
    def use_full_paths(answer):
        for match in answer['positions'][0]['matches']:
            for reference in match['evidence']:
                reference['field'] = f"{prefix}{reference['requirement_id']}.{reference['field']}"
        return answer

    demand = tender(need('Болт М12×95'))
    warehouse = stock(row('A', 'Болт М12', length_mm='95'))
    expected = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    actual = match_positions(demand, warehouse, SelectingModel(use_full_paths), 'test', lambda: None)
    assert expected.positions['1:0:1'].by_sku['A']
    assert actual.error is None
    assert actual.positions == expected.positions


@pytest.mark.parametrize('field', ['invented', 'evidence.family.invented', 'evidence.other.product',
                                   'family.invented', 'other.product'])
def test_wrong_cli_field_fails_the_batch(field):
    def hallucination(answer):
        answer['positions'][0]['matches'][0]['evidence'][0]['field'] = field
        return answer

    result = match_positions(tender(need('Болт М12')), stock(row('A', 'Болт М12')),
                             SelectingModel(hallucination), 'test', lambda: None)
    assert result.error['code'] == 'MODEL_INVALID_OUTPUT'
    assert result.positions['1:0:1'].uncertain
    assert not result.positions['1:0:1'].by_sku


def test_one_failed_batch_preserves_preceding_decisions_and_does_not_retry(monkeypatch):
    from bervel.modules.ai_tender_radar import matching

    monkeypatch.setattr(matching, 'MAX_POSITIONS', 1)

    class FailsAfterFirst(SelectingModel):
        def generate(self, *args, **kwargs):
            if self.calls:
                raise CoreError('MODEL_TIMEOUT', 'Исход неизвестен')
            return super().generate(*args, **kwargs)

    model = FailsAfterFirst()
    result = match_positions(tender(need('Болт М12'), need('Болт М12')), stock(row('A', 'Болт М12')),
                             model, 'test', lambda: None)
    assert len(model.calls) == 1
    assert result.positions['1:0:1'].by_sku
    assert not result.positions['1:0:2'].by_sku
    assert result.error['code'] == 'MODEL_TIMEOUT'


def test_invalid_batch_does_not_discard_other_batches(monkeypatch):
    from bervel.modules.ai_tender_radar import matching

    monkeypatch.setattr(matching, 'MAX_POSITIONS', 1)

    def corrupt_middle(answer):
        [position] = answer['positions']
        if position['id'] == '1:0:2':
            position['matches'][0]['evidence'][0]['field'] = 'invented'
        return answer

    model = SelectingModel(corrupt_middle)
    result = match_positions(tender(*(need('Болт М12') for _ in range(3))), stock(row('A', 'Болт М12')),
                             model, 'test', lambda: None)
    assert result.error['code'] == 'MODEL_INVALID_OUTPUT'
    assert result.positions['1:0:1'].by_sku
    assert result.positions['1:0:2'].reason == 'MODEL_NOT_COMPLETED'
    assert result.positions['1:0:3'].by_sku
    assert len(model.calls) == 3


def analyze(demand, warehouse):
    matching = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    return calculate_analysis(demand, warehouse, matching)


def test_allocates_shared_sku_once_and_can_reassign_to_cover_the_constrained_line():
    demand = tender(need('Болт М12', 10, PositionPrice=100), need('A', 10, PositionPrice=100))
    warehouse = stock(row('A', 'Болт М12'), row('B', 'Болт М12'))
    result = analyze(demand, warehouse)
    assert result['coverage_percent'] == '100'
    assert result['benefit_percent'] == '50'
    assert [line['allocations'][0]['sku'] for line in result['positions']] == ['B', 'A']


def test_partial_stock_uses_only_covered_revenue_and_cost():
    demand = tender(need('Болт М12', 100, ItemPrice=10), need('Гайка М12', 50, PositionPrice=400))
    result = analyze(demand, stock(row('B', 'Болт М12', free='80'), row('N', 'Гайка М12', free='50')))
    assert result['coverage_percent'].startswith('86.6666')
    assert result['benefit_percent'].startswith('45.8333')
    assert result['economics']['scope'] == 'confirmed'
    assert (result['economics']['revenue'], result['economics']['cost']) == ('1200', '650')
    assert result['positions'][0]['stock_status'] == 'partial'
    assert result['positions'][0]['economics']['margin_percent'] == '50'
    assert result['positions'][0]['economics']['scope'] == 'confirmed'
    assert result['positions'][1]['economics']['margin_percent'] == '37.5'


def test_limited_cost_data_prorates_the_line_total_and_skips_unpriced_volume():
    demand = tender(need('Болт М12', 100, PositionPrice=1000),
                    need('Гайка М12', 50, PositionPrice=10000), need('Шайба 20', 10))
    warehouse = stock(row('A', 'Болт М12', free='20', cost=None),
                      row('B', 'Болт М12', free='60', cost='12'),
                      row('W', 'Шайба', nominal_diameter_mm='20'))
    result = analyze(demand, warehouse)
    assert result['benefit_percent'] == '-20'
    assert result['economics']['scope'] == 'confirmed'
    assert (result['economics']['revenue'], result['economics']['cost']) == ('600', '720')
    assert result['positions'][0]['covered'] == '80'


def test_priced_lines_without_any_costed_allocation_cannot_produce_a_margin():
    result = analyze(tender(need('Болт М12', ItemPrice=10), need('Гайка М12')),
                     stock(row('N', 'Гайка М12')))
    assert result['benefit_percent'] is None
    assert result['economics']['scope'] is None


def test_loss_clamps_card_but_preserves_underlying_margin_and_zero_is_a_verdict():
    demand = tender(need('Болт М12', 10, PositionPrice=20), need('Гайка М12', 10, PositionPrice=10))
    result = analyze(demand, stock(row('B', 'Болт М12', cost='8'), row('N', 'Гайка М12', cost='1')))
    assert result['benefit_percent'] == '-100'
    assert result['economics']['margin_percent'] == '-200'
    assert result['economics']['profit'] == '-60'
    assert result['positions'][1]['economics']['status'] == 'break_even'


def test_mass_conversion_needs_confirmed_evidence_and_missing_price_remains_unknown():
    demand = tender(need('Болт М12', 2, 'кг'))
    known = analyze(demand, stock(row('B', 'Болт М12', weight_per_piece_kg='0.2')))
    assert known['coverage_percent'] == '100'
    assert known['positions'][0]['allocations'][0]['stock_quantity'] == '10'
    assert known['positions'][0]['economics']['reason'] == 'TENDER_PRICE_UNKNOWN'
    unknown = analyze(demand, stock(row('B', 'Болт М12')))
    assert unknown['coverage_percent'] is None
    assert unknown['positions'][0]['stock_status'] == 'unconfirmed'


def test_fractional_mass_uses_confirmed_sku_weight_and_costs_whole_pieces():
    demand = tender(need('Болт М12', 20, '166', ItemPrice=50))
    warehouse = stock(row('A', 'Болт М12', free='1000', theoretical_weight_per_piece_kg='0.2'),
                      row('B', 'Болт М12', free='125', weight_per_piece_kg='0.161'))
    result = analyze(demand, warehouse)
    line = result['positions'][0]
    assert line['mass_conversion']['weight_per_piece_kg'] == '0.161'
    assert line['mass_conversion']['required_pieces'].startswith('124.2236')
    assert [match['sku'] for match in line['matches']] == ['A', 'B']
    assert [(item['sku'], item['stock_quantity']) for item in line['allocations']] == [('B', '125')]
    assert line['covered'] == '20'
    assert line['shortage'] == '0'
    assert line['economics']['cost'] == '625'
    assert result['coverage_percent'] == '100'


def test_mass_and_piece_lines_share_inventory_without_fractional_allocations():
    demand = tender(need('Болт М12', 1), need('Болт М12', 20, 'кг'))
    result = analyze(demand, stock(row('B', 'Болт М12', free='125', weight_per_piece_kg='0.161')))
    pieces, mass = result['positions']
    assert pieces['allocations'][0]['stock_quantity'] == '1'
    assert mass['allocations'][0]['stock_quantity'] == '124'
    assert mass['covered'] == '19.964'
    assert mass['shortage'] == '0.036'
    assert mass['stock_status'] == 'partial'


def test_verified_weighted_sku_can_cover_demand_with_unconfirmed_alternatives():
    demand = tender(need('Болт М12', 20, 'кг',
                         Characteristics=[{'Name': 'Покрытие', 'Values': [{'Value': 'Цинк'}]}]))
    warehouse = stock(row('A', 'Болт М12', free='125', coating='Цинк', weight_per_piece_kg='0.161'),
                      row('B', 'Болт М12', coating_options='Цинк', weight_per_piece_kg='0.15'))
    matching = match_positions(demand, warehouse, SelectingModel(), 'test', lambda: None)
    assert not matching.positions['1:0:1'].uncertain
    line = calculate_analysis(demand, warehouse, matching)['positions'][0]
    assert line['stock_status'] == 'full'
    assert [item['sku'] for item in line['allocations']] == ['A']


def test_conflicting_confirmed_weights_leave_piece_conversion_unknown():
    result = analyze(tender(need('Болт М12', 20, 'кг')),
                     stock(row('A', 'Болт М12', weight_per_piece_kg='0.2'),
                           row('B', 'Болт М12', weight_per_piece_kg='0.161')))
    line = result['positions'][0]
    assert line['stock_status'] == 'unconfirmed'
    assert 'mass_conversion' not in line
    assert line['allocations'] == []


def test_confirmed_zero_is_distinct_from_unconfirmed_match():
    demand = tender(need('Болт М12'))
    assert analyze(demand, stock(row('B', 'Болт М16')))['coverage_percent'] == '0'
    unknown = analyze(demand, stock(row('B', 'Болт М12', free=None)))
    assert unknown['coverage_percent'] is None


def test_keeps_verified_sku_evidence_even_when_stock_is_zero():
    result = analyze(tender(need('Болт М12')), stock(row('B', 'Болт М12', free='0')))
    line = result['positions'][0]
    assert result['coverage_percent'] == '0'
    assert line['stock_reason'] == 'NO_STOCK'
    assert line['allocations'] == []
    assert line['matches'][0]['sku'] == 'B'
    assert line['matches'][0]['evidence'][0]['stock_value'] == 'Болт М12'


def test_conflicting_prices_and_currency_do_not_reduce_stock_coverage():
    priced = tender(need('Болт М12', ItemPrice=10, PositionPrice=102))
    conflict = analyze(priced, stock(row('B', 'Болт М12')))
    assert conflict['coverage_percent'] == '100'
    assert conflict['benefit_percent'] is None
    assert conflict['positions'][0]['economics']['reason'] == 'TENDER_PRICES_CONFLICT'
    foreign = analyze(tender(need('Болт М12', PositionPrice=100)), stock(row('B', 'Болт М12', currency='EUR')))
    assert foreign['coverage_percent'] == '100'
    assert foreign['benefit_percent'] is None
    assert foreign['positions'][0]['economics']['reason'] == 'STOCK_COST_OR_CURRENCY_UNKNOWN'


def test_partial_package_coverage_counts_exact_pieces_before_decimal_conversion():
    result = analyze(tender(need('Болт М12', 1, 'упаковка')),
                     stock(row('B', 'Болт М12', free='1', pieces_per_package='3')))
    assert result['coverage_percent'].startswith('33.3333')
    assert result['positions'][0]['stock_status'] == 'partial'
