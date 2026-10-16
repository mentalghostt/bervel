from pathlib import Path
from uuid import UUID

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/core-stage-3.1'


def parse(content):
    from bervel.core.models import ArtifactRevisionRef
    from bervel.modules.document_review.module import parse_csv
    return parse_csv(content, ArtifactRevisionRef(UUID('11111111-1111-4111-8111-111111111111'), 1))


def test_fixture_v1_preserves_unknown_and_evidence():
    result = parse((FIXTURES / 'requirements-v1.csv').read_bytes())
    assert result.quality_state == 'PARTIAL'
    assert len(result.records) == 2
    assert result.records[0].payload['quantity'] == {
        'state': 'KNOWN', 'value': '10', 'unit': 'piece',
        'original': {'value': '10', 'unit': 'pcs'},
    }
    second = result.records[1]
    assert second.payload['name'] == 'Тестовое изделие B'
    assert second.payload['quantity']['value'] is None
    assert second.payload['quantity']['state'] == 'UNKNOWN'
    assert second.issues[0]['code'] == 'MISSING_QUANTITY'
    assert second.evidence_refs[1]['locator'] == {'kind': 'csv_cell', 'row': 3, 'column': 'quantity'}
    assert second.evidence_refs[1]['revision'] == 1
    assert second.evidence_refs[1]['role'] == 'test_fixture'


def test_fixture_v2_and_stable_row_identity():
    first = parse((FIXTURES / 'requirements-v1.csv').read_bytes())
    second = parse((FIXTURES / 'requirements-v2.csv').read_bytes())
    assert second.quality_state == 'COMPLETE'
    assert second.records[1].payload['quantity']['value'] == '4'
    assert [r.record_id for r in first.records] == [r.record_id for r in second.records]


@pytest.mark.parametrize('value,unit,want', [('0', 'pcs', '0'), ('3.500', 'piece', '3.500')])
def test_bom_crlf_zero_and_decimal(value, unit, want):
    record = parse(('\ufeffline,name,quantity,unit\r\n1,Name,' + value + ',' + unit + '\r\n').encode()).records[0]
    assert record.quality_state == 'COMPLETE'
    assert record.payload['quantity']['value'] == want
    assert record.payload['quantity']['original']['unit'] == unit


@pytest.mark.parametrize('content', [
    b'line,name,quantity,name\n1,A,1,pcs\n',
    b'line,name,quantity,unit,extra\n1,A,1,pcs,x\n',
    b'line,name,quantity,unit\n1,A,1,pcs,x\n',
    b'line,name,quantity,unit\n1,A,1,pcs\n1,B,2,pcs\n',
    b'line,name,quantity,unit\n0,A,1,pcs\n',
    b'line,name,quantity,unit\n1,,1,pcs\n',
    b'line,name,quantity,unit\n1,A,-1,pcs\n',
    b'line,name,quantity,unit\n1,A,NaN,pcs\n',
    b'line,name,quantity,unit\n1,A,Infinity,pcs\n',
    b'line,name,quantity,unit\n1,A,1e3,pcs\n',
    b'line,name,quantity,unit\n1,A,1,kg\n',
    b'line,name,quantity,unit\n1,A,1\n',
    b'line,name,quantity,unit\n1,"unclosed,1,pcs\n',
    b'line,name,quantity,unit\n1,\xff,1,pcs\n',
    b'line,name,quantity,unit\n',
    ('line,name,quantity,unit\n1,' + 'x' * 2001 + ',1,pcs\n').encode(),
    ('line,name,quantity,unit\n' + ''.join(f'{i},A,1,pcs\n' for i in range(1, 1002))).encode(),
    b'x' * (5 * 1024 * 1024 + 1),
], ids=[f'invalid-{i}' for i in range(18)])
def test_invalid_inputs_do_not_produce_a_result(content):
    from bervel.core.errors import CoreError
    with pytest.raises(CoreError) as error:
        parse(content)
    assert error.value.code in {'VALIDATION_ERROR', 'QUOTA_EXCEEDED'}
