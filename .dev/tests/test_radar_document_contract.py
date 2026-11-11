from copy import deepcopy

import pytest

from bervel.core.errors import CoreError
from bervel.core.model_contract import validate
from bervel.modules.ai_tender_radar.documents import OUTPUT_SCHEMA, enrich_tender
from test_ai_radar_documents import ExtractionModel, TEXT, answer, fact, tender


@pytest.mark.parametrize('field', ['name', 'value'])
def test_model_schema_rejects_a_missing_required_characteristic_fact(field):
    schema = OUTPUT_SCHEMA['properties']['requirements']['items']
    row = {'position_id': '1:0:1', 'name': fact('Диаметр', 'Диаметр 12'),
           'value': fact('12', 'Диаметр 12'), 'unit': None}
    validate(row, schema)
    row[field] = None
    with pytest.raises(CoreError):
        validate(row, schema)


@pytest.mark.parametrize('value,unit,accepted', [('12', None, True), ('12', 'мм', True),
                                               ('13', None, False), ('12', 'см', False)])
def test_repeated_document_value_retains_authoritative_structured_unit(value, unit, accepted):
    quote = f'Диаметр резьбы: {value}' + (f' {unit}' if unit else '')
    source = tender(TEXT + f'\nДля позиции Болт М12 обязательно {quote}.')
    original = {'Name': 'Диаметр резьбы', 'FillingInstruction': 'Из структурированного источника',
                'Values': [{'Value': '12', 'Okei': 'мм'}]}
    source['positions'][0]['characteristics'] = [deepcopy(original)]
    before = deepcopy(source)
    output = answer()
    output['requirements'].append({'position_id': '1:0:1', 'name': fact('Диаметр резьбы', quote),
                                  'value': fact(value, quote), 'unit': fact(unit, quote) if unit else None})
    result = enrich_tender(source, ExtractionModel(output), 'doc-unit-contract', lambda: None,
                           profile={'document_timezone': 'Europe/Moscow'})
    assert source == before
    if accepted:
        assert result['document_extraction']['state'] == 'complete'
        features = [f for f in result['positions'][0]['characteristics'] if f['Name'] == 'Диаметр резьбы']
        assert features == [original]
        assert result['positions'][0]['document_evidence']
    else:
        assert result['document_extraction']['state'] == 'unknown'
        assert 'противоречит' in result['document_extraction']['error']['message']
        assert result['positions'] == before['positions']


@pytest.mark.parametrize('source_value,source_unit,quote,accepted', [
    ('12', 'см', 'Диаметр резьбы: 12 см', False),
    ('123', 'мм', 'Диаметр резьбы: 123 мм', False),
    ('12', 'см', '12', False),
    ('12', 'мм', 'Диаметр резьбы: 12 мм', True),
    ('12', 'мм', '12', True),
    ('12.5', 'мм', '12', False),
])
def test_omitted_unit_checks_whole_source_tokens(source_value, source_unit, quote, accepted):
    line = f'Для позиции Болт М12 обязательно Диаметр резьбы: {source_value} {source_unit}.'
    source = tender(TEXT + '\n' + line)
    original = {'Name': 'Диаметр резьбы', 'FillingInstruction': 'Из структурированного источника',
                'Values': [{'Value': '12', 'Okei': 'мм'}]}
    source['positions'][0]['characteristics'] = [deepcopy(original)]
    before = deepcopy(source)
    output = answer()
    output['requirements'].append({'position_id': '1:0:1',
        'name': fact('Диаметр резьбы', line), 'value': fact('12', quote), 'unit': None})
    result = enrich_tender(source, ExtractionModel(output), 'omitted-unit-conflict', lambda: None,
                           profile={'document_timezone': 'Europe/Moscow'})
    assert result['document_extraction']['state'] == ('complete' if accepted else 'unknown')
    if accepted:
        features = [f for f in result['positions'][0]['characteristics'] if f['Name'] == original['Name']]
        assert features == [original]
        assert result['positions'][0]['document_evidence']
    else:
        assert result['positions'] == before['positions']
    assert source == before
