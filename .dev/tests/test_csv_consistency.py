"""Preview and retrieval must agree on bytes, cells and bounded coverage."""
import csv
import io
import json

import pytest

from bervel.core.infrastructure.document_reader import DocumentReader
from bervel.core.infrastructure.file_preview import preview


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'cp1251', 'utf-16'])
@pytest.mark.parametrize('delimiter', [',', ';', '\t', '|'])
def test_preview_and_index_preserve_the_same_csv_cells(encoding, delimiter):
    stream = io.StringIO(newline='')
    rows = [['Код', 'Название', 'Количество'], ['0016', 'Болт; ГОСТ\n7798', '9007199254740993.1234']]
    csv.writer(stream, delimiter=delimiter).writerows(rows)
    content = stream.getvalue().encode(encoding)
    viewed = preview(content, 'source.csv', 'text/csv')
    indexed = DocumentReader().extract(content, 'source.csv', 'text/csv')
    assert viewed['kind'] == 'table' and viewed['columns'] == rows[0]
    assert viewed['rows'] == rows[1:]
    assert indexed['status'] == 'ready'
    excerpt = '\n'.join(chunk['excerpt'] for chunk in indexed['chunks'])
    assert json.dumps(rows[0], ensure_ascii=False) in excerpt
    assert json.dumps(rows[1], ensure_ascii=False) in excerpt
    assert indexed['chunks'][0]['row_start'] == indexed['chunks'][0]['row_end'] == 1


def test_index_honestly_marks_shared_row_limit_and_rejects_excess_columns():
    content = ('id\n' + '1\n' * 10001).encode()
    indexed = DocumentReader().extract(content, 'large.csv', 'text/csv')
    assert indexed['status'] == 'partial'
    assert all(chunk['total_rows'] == 10000 for chunk in indexed['chunks'])
    assert DocumentReader().extract(('h,' * 201 + '\nx').encode(), 'wide.csv', 'text/csv')['status'] == 'metadata_only'


def test_delimiter_detection_handles_a_sample_cut_inside_a_quoted_multiline_cell():
    cell = ('a' * 250 + ',b,c\n') * 35
    content = ('id;description;value\n1;"' + cell + '";482\n').encode()
    viewed = preview(content, 'long.csv', 'text/csv')
    assert viewed['kind'] == 'table'
    assert viewed['columns'] == ['id', 'description', 'value']
    assert viewed['rows'] == [['1', cell, '482']]
    indexed = DocumentReader().extract(content, 'long.csv', 'text/csv')
    assert indexed['status'] == 'ready'
    assert '482' in ''.join(chunk['excerpt'] for chunk in indexed['chunks'])
