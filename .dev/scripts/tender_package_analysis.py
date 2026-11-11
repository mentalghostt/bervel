"""Re-read customer originals, verify intake manifest, summarize radar evidence."""
from pathlib import Path
from hashlib import sha256
import json
import re
from collections import Counter
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import openpyxl
from pypdf import PdfReader

root = Path(__file__).resolve().parents[2]
folder = root / '.dev/data/private/customer-package-2026-10-06'
output = root / '.dev/local/tender-radar'
output.mkdir(parents=True, exist_ok=True)
manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
result = {'files': [], 'workbooks': {}, 'documents': {}}
for expected in manifest:
    path = folder / expected['file']
    digest = sha256(path.read_bytes()).hexdigest()
    assert digest == expected['sha256'] and path.stat().st_size == expected['bytes'], path.name
    result['files'].append({'file': path.name, 'sha256': digest, 'bytes': path.stat().st_size, 'verified': True})
    if path.suffix.lower() == '.xlsx':
        book = openpyxl.load_workbook(path, data_only=False, keep_links=False)
        sheets = []
        for sheet in book:
            records, hits = [], []
            for row in sheet:
                values = {cell.column_letter: {'cell': cell.coordinate, 'value': cell.value.isoformat() if hasattr(cell.value, 'isoformat') else cell.value,
                    'link': cell.hyperlink.target if cell.hyperlink else None} for cell in row if cell.value is not None}
                if values: records.append({'row': row[0].row, 'cells': values})
                title = str(sheet.cell(row[0].row, 3).value or '')
                if re.search('болт|гайк|шайб|креп[её]ж|метиз', title, re.I):
                    hits.append({'row': row[0].row, 'title': title, 'number': sheet.cell(row[0].row, 1).value,
                        'deadline': str(sheet.cell(row[0].row, 7).value), 'url': sheet.cell(row[0].row, 1).hyperlink.target if sheet.cell(row[0].row, 1).hyperlink else None})
            sheets.append({'sheet': sheet.title, 'rows': sheet.max_row, 'columns': sheet.max_column,
                'header_preview': records[:5], 'fastener_title_count': len(hits), 'fastener_preview': hits[:20]})
            (output / (path.stem + '-rows.json')).write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        result['workbooks'][path.name] = sheets
        book.close()
    elif path.suffix.lower() == '.docx':
        with ZipFile(path) as archive:
            tree = ET.fromstring(archive.read('word/document.xml'))
        text = '\n'.join(''.join(node.itertext()) for node in tree.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
        (output / (path.name + '.txt')).write_text(text, encoding='utf-8')
        result['documents'][path.name] = {'characters': len(text), 'excerpt': text[:300]}
    elif path.suffix.lower() == '.pdf':
        reader = PdfReader(path)
        text = '\n'.join(f'PAGE {i + 1}\n' + (page.extract_text() or '') for i, page in enumerate(reader.pages))
        (output / (path.name + '.txt')).write_text(text, encoding='utf-8')
        result['documents'][path.name] = {'pages': len(reader.pages), 'characters': len(text), 'excerpt': text[:200]}
(output / 'intake.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print(json.dumps({'verified_originals': len(result['files']), 'workbooks': {name: [{'sheet': s['sheet'], 'rows': s['rows'], 'columns': s['columns'], 'fastener_title_count': s['fastener_title_count']} for s in sheets] for name, sheets in result['workbooks'].items()}}, ensure_ascii=False))
