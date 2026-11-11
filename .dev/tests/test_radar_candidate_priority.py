from bervel.modules.ai_tender_radar.documents import enrich_tender
from bervel.modules.ai_tender_radar.prefilter import CatalogPrefilter
from test_ai_radar_documents import ExtractionModel, answer, tender
from test_ai_tender_radar_analysis import row, stock


def test_separate_product_mentions_accumulate_without_counting_catalog_variants():
    warehouse = stock(row('B', 'Болт М12'), row('B2', 'Болт М16'), row('N', 'Гайка М12'))
    profile = {'products': [dict(id=r['sku'], sku=r['sku'], name=r['product'], enabled=True)
                            for r in warehouse['rows']]}
    candidates = [{'id': 'one', 'brief': {'OrderName': 'Поставка болтов для ремонта'}},
                  {'id': 'two', 'brief': {'OrderName': 'Поставка болтов и гаек для ремонта'}},
                  {'id': 'work', 'brief': {'OrderName': 'Ремонт станка с заменой болтов и гаек'}}]
    ranked = CatalogPrefilter(warehouse, profile).rank(candidates, strategy='catalog-prefilter-v6')
    assert [r['id'] for r in ranked] == ['two', 'one', 'work']
    assert [r['rank_metadata']['product_mentions'] for r in ranked] == [2, 1, 2]


def test_document_dimensions_are_checked_only_against_their_named_rows():
    source = tender('Наименование: Болт М12; количество: 1.0E+1; единица: шт.\nДлина: 40\n'
                    'Наименование: Болт М16; количество: 10; единица: шт.\nДлина: 60\n'
                    'Для позиции Болт М16 обязательно покрытие: Цинк.')
    first = source['positions'][0]
    first['characteristics'] = [{'Name': 'Длина', 'Values': [{'Value': '40', 'Okei': 'мм'}]}]
    source['positions'].append({**first, 'id': '2', 'name': 'Болт М16', 'characteristics': [
        {'Name': 'Длина', 'Values': [{'Value': '60', 'Okei': 'мм'}]}]})
    output = answer()
    output['terms'] = dict.fromkeys(output['terms'])
    output['requirements'][0]['position_id'] = '2'
    model = ExtractionModel(output)
    result = enrich_tender(source, model, 'scoped', lambda: None, prompt_version='document-extraction-v3')
    assert result['document_extraction']['state'] == 'complete'
    assert 'Длина: 40' not in model.calls[0]['documents'][0]['text']
    assert result['positions'][1]['characteristics'][-1]['Name'] == 'Покрытие'
    source['documents'][0]['text'] = source['documents'][0]['text'].replace('Длина: 40', 'Длина: 45')
    result = enrich_tender(source, model, 'conflict', lambda: None, prompt_version='document-extraction-v3')
    assert result['document_extraction']['state'] == 'unknown'

    # A following global clause still requires a fact for both positions.
    source['documents'][0]['text'] = source['documents'][0]['text'].replace('Длина: 45', 'Длина: 40').replace(
        'Для позиции Болт М16 обязательно покрытие', 'Обязательное покрытие для всех изделий')
    output['requirements'][0]['name']['quote'] = 'покрытие для всех изделий: Цинк'
    output['requirements'][0]['value']['quote'] = 'покрытие для всех изделий: Цинк'
    result = enrich_tender(source, model, 'global', lambda: None, prompt_version='document-extraction-v3')
    assert result['document_extraction']['state'] == 'unknown'
