import csv
from decimal import Decimal
import io
import re
from uuid import uuid5

from bervel.core.errors import CoreError
from bervel.core.models import ArtifactRevisionRef, DataRecord, ExtractionResult

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 1000
MAX_CELL = 2000


def parse_csv(content: bytes, artifact_ref: ArtifactRevisionRef) -> ExtractionResult:
    if len(content) > MAX_BYTES:
        raise CoreError('QUOTA_EXCEEDED', 'CSV exceeds 5 MiB')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise CoreError('VALIDATION_ERROR', 'CSV must use UTF-8') from error
    if '\x00' in text:
        raise CoreError('VALIDATION_ERROR', 'CSV contains a null byte')
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    records = []
    seen = set()
    try:
        if next(reader, None) != ['line', 'name', 'quantity', 'unit']:
            raise CoreError('VALIDATION_ERROR', 'Expected columns: line,name,quantity,unit')
        while True:
            row_number = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if len(records) >= MAX_ROWS:
                raise CoreError('QUOTA_EXCEEDED', 'CSV exceeds 1000 data rows')
            if len(row) != 4 or any(len(cell) > MAX_CELL for cell in row):
                raise CoreError('VALIDATION_ERROR', f'Invalid cells at CSV row {row_number}')
            line, name, quantity, unit = row
            if not re.fullmatch(r'[1-9][0-9]*', line) or int(line) in seen:
                raise CoreError('VALIDATION_ERROR', f'Invalid or duplicate line at CSV row {row_number}')
            seen.add(int(line))
            if not name.strip() or unit not in ('pcs', 'piece'):
                raise CoreError('VALIDATION_ERROR', f'Invalid name or unit at CSV row {row_number}')
            if quantity and not re.fullmatch(r'(0|[1-9][0-9]*)(\.[0-9]+)?', quantity):
                raise CoreError('VALIDATION_ERROR', f'Invalid quantity at CSV row {row_number}')
            value = format(Decimal(quantity), 'f') if quantity else None
            quality = 'COMPLETE' if value is not None else 'PARTIAL'
            evidence = [
                {'artifact_id': str(artifact_ref.artifact_id), 'revision': artifact_ref.revision,
                 'role': 'test_fixture', 'field_path': path,
                 'locator': {'kind': 'csv_cell', 'row': row_number, 'column': column}}
                for path, column in [('/name', 'name'), ('/quantity/value', 'quantity'), ('/quantity/unit', 'unit')]
            ]
            issues = [] if value is not None else [
                {'code': 'MISSING_QUANTITY', 'field_path': '/quantity/value', 'message': 'Quantity is absent in source'}
            ]
            records.append(DataRecord(str(uuid5(artifact_ref.artifact_id, f'line:{line}')), quality,
                {'name': name.strip(), 'quantity': {'state': 'KNOWN' if value is not None else 'UNKNOWN',
                 'value': value, 'unit': 'piece', 'original': {'value': quantity, 'unit': unit}}}, evidence, issues))
    except csv.Error as error:
        raise CoreError('VALIDATION_ERROR', 'Malformed CSV') from error
    if not records:
        raise CoreError('VALIDATION_ERROR', 'CSV has no data rows')
    return ExtractionResult('demo.document-review.requirement-row', '1.0.0',
        'PARTIAL' if any(r.quality_state == 'PARTIAL' for r in records) else 'COMPLETE', records)
