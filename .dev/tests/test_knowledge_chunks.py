"""Catch lost table context, split facts, wrong locators and unbounded API inputs."""
import json


def test_short_paragraphs_stay_together_and_keep_page_locator():
    from bervel.core.chunking import split_blocks
    text = 'Условия поставки.\n\nПоставка только комплектом: один болт, одна гайка, две шайбы.'
    result = split_blocks([dict(excerpt=text, unit='page', row_start=7, row_end=7, total_rows=12)])
    assert len(result) == 1 and result[0]['excerpt'] == text
    assert result[0]['row_start'] == result[0]['row_end'] == 7


def test_long_text_is_fully_covered_and_split_on_paragraphs():
    from bervel.core.chunking import split_blocks
    paragraphs = ['Правило %s: %s.' % (i, 'проверить покрытие и прочность ' * 12) for i in range(12)]
    text = '\n\n'.join(paragraphs)
    result = split_blocks([dict(excerpt=text, unit='line', row_start=1, row_end=23, total_rows=23)])
    assert len(result) > 1
    assert ''.join(chunk['excerpt'] for chunk in result) == text
    assert all(len(chunk['excerpt'].encode()) <= 4500 for chunk in result)
    assert all(any(chunk['excerpt'].startswith(p[:20]) for p in paragraphs) for chunk in result)


def test_csv_records_keep_header_and_exact_row_without_merging_products():
    from bervel.core.chunking import table_blocks
    rows = [['0016', 'Болт', '17'], ['0017', 'Гайка', '83']]
    result = table_blocks(['Код', 'Изделие', 'Количество'], rows)
    assert len(result) == 2
    assert 'Количество' in result[1]['excerpt'] and '83' in result[1]['excerpt']
    assert '0016' not in result[1]['excerpt']
    assert result[1]['row_start'] == result[1]['row_end'] == 2


def test_very_long_unicode_row_retains_all_cells_and_bounds_embeddings():
    from bervel.core.chunking import table_blocks, embedding_input
    text = '🔩' * 5000 + 'END_OF_ROW'
    result = table_blocks(['Описание'], [[text]])
    assert len(result) > 1
    assert any('END_OF_ROW' in chunk['excerpt'] for chunk in result)
    assert all(len(embedding_input('профиль.csv', chunk).encode()) <= 6000 for chunk in result)


def test_embedding_input_preserves_context_and_is_stable_under_upload_alias():
    from bervel.core.chunking import embedding_input
    value = embedding_input('production.csv',dict(excerpt='Изделие: Болт М16',unit='row',row_start=4,row_end=4))
    assert 'Болт М16' in value and '4' in value
    assert value==embedding_input('another-name.csv',dict(excerpt='Изделие: Болт М16',unit='row',row_start=4,row_end=4))
