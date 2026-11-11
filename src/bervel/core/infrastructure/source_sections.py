"""Full sequential source reading; budgets apply to individual units, never files."""
import csv
from hashlib import sha256
from io import BytesIO, StringIO
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from typing import Iterator
from zipfile import ZipFile

from defusedxml.ElementTree import fromstring
from bervel.core.document_models import SourceSection


def source_workspace(workspace: Path | None = None) -> Path:
    path = workspace or Path(__file__).resolve().parents[4] / '.dev/local/presales-pipeline-2026-11-10/documents'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run(args: list[str]) -> str:
    # No shell and no total file-size/time cap. A failed page remains visible.
    result = subprocess.run(args, capture_output=True, check=True, timeout=120)
    return result.stdout.decode('utf-8', errors='strict')


def _ocr_pages(path: Path) -> list[tuple[int, str, list[str]]]:
    output = _run(['tesseract', str(path), 'stdout', '-l', 'rus+eng', 'tsv'])
    rows = list(csv.DictReader(StringIO(output), delimiter='\t'))
    pages=sorted({int(row.get('page_num','1')) for row in rows}) or [1]
    result=[]
    for page in pages:
        words = [row for row in rows if int(row.get('page_num','1'))==page and row.get('text', '').strip()]
        lines: dict[tuple, list[str]] = {}; uncertain=False
        for row in words:
            key = tuple(row.get(k) for k in ('block_num', 'par_num', 'line_num'))
            lines.setdefault(key, []).append(row['text'])
            uncertain |= float(row.get('conf', '-1')) < 60
        text = '\n'.join(' '.join(words) for words in lines.values())
        issues = ['Распознавание содержит неуверенные слова; требуется сверка оригинала.'] if uncertain else []
        if not text.strip(): issues.append('Распознаваемый текст отсутствует; визуальное содержимое не подтверждено.')
        result.append((page,text,issues))
    return result


def _ocr(path: Path) -> tuple[str,list[str]]:
    pages=_ocr_pages(path)
    return '\n'.join(text for _,text,_ in pages),[issue for _,_,issues in pages for issue in issues]


def iter_source_sections(content: bytes, filename: str, *, artifact_id: str = '',
                         workspace: Path | None = None) -> Iterator[SourceSection]:
    """Visit all XLSX sheets/rows, CSV rows, DOCX blocks and PDF/image pages."""
    digest = sha256(content).hexdigest()
    def section(text, unit, location, issues=None):
        issues = issues or []
        identity = json.dumps([artifact_id, digest, unit, location], sort_keys=True, ensure_ascii=False)
        return SourceSection(section_id=sha256(identity.encode()).hexdigest(), artifact_id=artifact_id,
            filename=filename, text=text, unit=unit, location=location, complete=not issues, issues=issues)
    name = filename.casefold()
    visited = False
    try:
        if name.endswith('.xlsx'):
            from openpyxl import load_workbook
            book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
            cached = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
            try:
                for sheet in book:
                    sheet.reset_dimensions(); values_sheet = cached[sheet.title]; values_sheet.reset_dimensions()
                    header = ''
                    for number, (row, values) in enumerate(zip(sheet, values_sheet), 1):
                        parts, issues = [], []
                        for cell, value_cell in zip(row, values):
                            value = cell.value
                            if value is None: continue
                            if cell.data_type == 'f':
                                value = value_cell.value
                                if value is None:
                                    issues.append(f'Ячейка {cell.coordinate}: у формулы нет сохранённого результата.')
                                    value = '[FORMULA_RESULT_UNKNOWN]'
                                elif value_cell.data_type == 'e':
                                    issues.append(f'Ячейка {cell.coordinate}: сохранённый результат формулы содержит ошибку Excel.')
                            if cell.data_type == 'e': issues.append(f'Ячейка {cell.coordinate}: ошибка Excel.')
                            parts.append(f'{cell.coordinate}: {value}')
                        if not parts: continue
                        text = ' | '.join(parts)
                        if not header: header = text
                        elif header: text = 'Заголовок листа: ' + header + '\n' + text
                        visited = True
                        yield section(text, 'row', {'sheet': sheet.title, 'row': number}, issues)
            finally:
                book.close(); cached.close()
        elif name.endswith('.docx'):
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            with ZipFile(BytesIO(content)) as archive:
                names = ['word/document.xml'] + sorted(n for n in archive.namelist()
                    if re.fullmatch(r'word/(header\d+|footer\d+|footnotes|endnotes)\.xml', n))
                for part in names:
                    root = fromstring(archive.read(part)); table_count, paragraph_count = 0, 0
                    body = root.find(ns+'body') if part == 'word/document.xml' else root
                    if body is None: continue
                    for block in body:
                        if block.tag == ns+'tbl':
                            table_count += 1
                            for number, row in enumerate(block.findall(ns+'tr'), 1):
                                cells = ['\n'.join(''.join(t.text or '' for t in p.iter(ns+'t')) for p in cell.iter(ns+'p')) for cell in row.findall(ns+'tc')]
                                location = {'table': table_count, 'row': number}
                                if part != 'word/document.xml': location['part'] = part
                                visited = True
                                issues = ['Встроенное изображение требует отдельного визуального разбора.'] if list(row.iter(ns+'drawing')) else []
                                yield section(' | '.join(cells), 'row', location, issues)
                        else:
                            for p in ([block] if block.tag == ns+'p' else block.iter(ns+'p')):
                                paragraph_count += 1
                                text = ''.join(t.text or '' for t in p.iter(ns+'t'))
                                issues = ['Встроенное изображение требует отдельного визуального разбора.'] if list(p.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing')) else []
                                if text.strip() or issues:
                                    visited = True
                                    yield section(text, 'line', {'part': part, 'paragraph': paragraph_count}, issues)
                # Images embedded in tables/body/header are visited even when a text layer exists.
                for media in sorted(n for n in archive.namelist() if n.startswith('word/media/')):
                    with TemporaryDirectory(prefix='source-',dir=source_workspace(workspace)) as folder:
                        path=Path(folder)/('embedded'+Path(media).suffix); path.write_bytes(archive.read(media))
                        try:
                            for page,text,issues in _ocr_pages(path):
                                visited=True
                                yield section(text,'image',{'part':media,'page':page},issues)
                        except (OSError,ValueError,subprocess.SubprocessError) as error:
                            yield section('','image',{'part':media},[f'Встроенное изображение не распознано: {type(error).__name__}.'])
        elif name.endswith('.csv'):
            text = None
            encodings=('utf-32',) if content.startswith((b'\xff\xfe\x00\x00',b'\x00\x00\xfe\xff')) else (('utf-16',) if content.startswith((b'\xff\xfe',b'\xfe\xff')) else ('utf-8-sig','cp1251'))
            for encoding in encodings:
                try: text = content.decode(encoding); break
                except UnicodeError: continue
            if text is None: raise ValueError('Неизвестная кодировка CSV.')
            if '\x00' in text: raise ValueError('CSV содержит бинарные или повреждённые данные.')
            try: dialect = csv.Sniffer().sniff(text[:16384], delimiters=',;\t|')
            except csv.Error: dialect = csv.excel
            header = None
            previous_limit=csv.field_size_limit()
            try:
                csv.field_size_limit(max(previous_limit,len(text)))
                for row in csv.reader(StringIO(text, newline=''), dialect, strict=True):
                    # enumerate record coordinates, including quoted multiline cells.
                    if header is None: header = row; number = 1
                    else: number += 1
                    visited = True
                    yield section(('Заголовок: '+' | '.join(header)+'\n' if number > 1 else '') + ' | '.join(row), 'row', {'row': number})
            finally:
                csv.field_size_limit(previous_limit)
        elif content.startswith(b'%PDF-') or name.endswith('.pdf'):
            with TemporaryDirectory(prefix='source-', dir=source_workspace(workspace)) as folder:
                path = Path(folder)/'source.pdf'; path.write_bytes(content)
                info = _run(['pdfinfo', str(path)])
                match = re.search(r'^Pages:\s+(\d+)', info, re.MULTILINE)
                if not match: raise ValueError('Не определено количество страниц PDF.')
                for number in range(1, int(match[1])+1):
                    issues=[]; text=''
                    try:
                        text = _run(['pdftotext','-f',str(number),'-l',str(number),'-layout','-enc','UTF-8',str(path),'-']).replace('\f','').strip()
                        # Text and scans can coexist on a page. Inspect raster objects too.
                        images = _run(['pdfimages','-f',str(number),'-l',str(number),'-list',str(path)])
                        raster = any(re.match(r'^\s*\d+\s+\d+\s+image\s', line) for line in images.splitlines())
                        if not text or raster:
                            prefix=Path(folder)/f'page-{number}'
                            _run(['pdftoppm','-f',str(number),'-l',str(number),'-r','200','-singlefile','-png',str(path),str(prefix)])
                            ocr, issues = _ocr(prefix.with_suffix('.png'))
                            text = text + ('\n[OCR]\n'+ocr if text else ocr)
                            prefix.with_suffix('.png').unlink(missing_ok=True)
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        issues.append(f'Страница не прочитана полностью: {type(error).__name__}.')
                    visited=True
                    yield section(text, 'page', {'page': number}, issues)
        elif name.endswith(('.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp','.gif')):
            with TemporaryDirectory(prefix='source-', dir=source_workspace(workspace)) as folder:
                path=Path(folder)/('source'+Path(name).suffix); path.write_bytes(content)
                for page,text,issues in _ocr_pages(path):
                    visited=True
                    yield section(text, 'image', {'image': 1,'page':page}, issues)
        else:
            text=content.decode('utf-8-sig')
            if '\x00' in text: raise ValueError('Бинарный формат не поддерживается.')
            for number, line in enumerate(text.splitlines(), 1):
                visited=True
                yield section(line, 'line', {'line': number})
        if not visited: yield section('', 'line', {}, ['В файле не найдено содержимого для разбора.'])
    except Exception as error:
        # File boundary: retain preceding units and a visible failed tail, never lose a request.
        yield section('', 'line', {'unread_tail': True}, [f'Файл не прочитан полностью: {type(error).__name__}.'])
