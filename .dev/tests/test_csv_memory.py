import pytest

from bervel.core.csv_memory import parse_csv
from bervel.core.errors import CoreError
from test_document_memory import documents, add, ask


@pytest.mark.parametrize('delimiter', [';', '\t'])
def test_long_middle_cell_does_not_change_separator_or_merge_columns(delimiter):
    content = delimiter.join(['id', 'description', 'value']) + '\n' + delimiter.join(['1', 'x' * 10000, '482']) + '\n'
    chunks = parse_csv(content.encode())
    assert all('Columns: ["id", "description", "value"]' in chunk['excerpt'] for chunk in chunks)
    assert '482"]' in ''.join(chunk['excerpt'] for chunk in chunks)
    assert all(chunk['total_rows'] == 1 for chunk in chunks)


def test_raw_utf8_columns_and_quoted_rows_are_preserved():
    chunks = parse_csv('Код;Неизвестная колонка;Текст\n1;Жёлтый;"две\nстроки"\n2;Синий;да\n'.encode())
    sources = chunks
    assert sources[0]['total_rows'] == 2
    assert 'Неизвестная колонка' in sources[0]['excerpt']
    assert 'Жёлтый' in sources[0]['excerpt'] and 'две\\nстроки' in sources[0]['excerpt']
    assert sources[0]['row_start'] == 1 and sources[0]['row_end'] == 2
    assert sources[0]['truncated'] is False


def test_large_csv_retrieves_real_late_row_and_marks_partial_context(documents):
    service, artifacts, actor, _, _ = documents
    add(artifacts, actor, 'big.csv', ('id,detail\n' + ''.join(f'{n},значение{n}\n' for n in range(5000))).encode(), 'text/csv')
    sources = ask(service, actor, 'значение4998')['sources']
    assert any('значение4998' in source['excerpt'] for source in sources)
    assert all(source['total_rows'] == 5000 and source['truncated'] for source in sources)
    assert sum(len(s['excerpt']) for s in sources) <= 16000


def test_huge_cell_and_header_stay_bounded_without_claiming_full_coverage(documents):
    service, artifacts, actor, _, _ = documents
    add(artifacts, actor, 'big.csv', ('column,' + 'h' * 10000 + '\n1,' + 'Ж' * 50000).encode(), 'text/csv')
    sources = ask(service, actor, 'Ж')['sources']
    assert sources and all(s['truncated'] for s in sources)
    assert sum(len(s['excerpt']) for s in sources) <= 16000


@pytest.mark.parametrize('content', [b'\xff\xfe', b'', b'"unterminated'])
def test_invalid_csv_is_an_explicit_error(content):
    with pytest.raises(CoreError):
        parse_csv(content)


def test_header_only_csv_is_explicitly_empty():
    sources = parse_csv(b'a,b\n')
    assert sources[0]['total_rows'] == 0 and sources[0]['row_start'] == 0 and sources[0]['row_end'] == 0
