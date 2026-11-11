"""Bounded local decoding/OCR. User filenames are never executable paths."""
import os
from pathlib import Path
import re
import resource
import subprocess
from tempfile import TemporaryDirectory, TemporaryFile
from time import monotonic

from bervel.core.csv_memory import read_csv
from bervel.core.chunking import split_blocks, table_blocks
from bervel.core.errors import CoreError


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024,) * 2)


def command(args: list[str], *, timeout: float = 40) -> str:
    # File-size limits also bound child output, before the parent reads it.
    with TemporaryFile() as output, TemporaryFile() as errors:
        subprocess.run(args, stdout=output, stderr=errors, timeout=timeout, check=True,
                       env={**os.environ, 'LC_ALL': 'C', 'OMP_THREAD_LIMIT': '1'}, preexec_fn=limits)
        output.seek(0)
        content = output.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise ValueError('Extracted output exceeds limit')
    return content.decode('utf-8', errors='replace')


def text_chunks(text: str, unit: str, start: int, total: int) -> list[dict]:
    return split_blocks([{'row_start':start,'row_end':start,'total_rows':total,'unit':unit,'excerpt':text[:5000000]}])


def unavailable(reason: str) -> dict:
    return {'status': 'metadata_only', 'chunks': [{'row_start': 0, 'row_end': 0, 'total_rows': 0,
        'unit': 'metadata', 'excerpt': 'Содержимое не прочитано: ' + reason}]}


class DocumentReader:
    def table(self, content: bytes, filename: str, media_type: str, *, sheet: str | None = None) -> dict:
        """Decode exact cells; a bounded or ambiguous table never masquerades as complete."""
        if filename.casefold().endswith('.xlsx'):
            from bervel.core.infrastructure.office_reader import office_table
            return office_table(content, sheet=sheet)
        if filename.casefold().endswith('.csv') or media_type in {'text/csv', 'application/csv'}:
            if sheet is not None:
                raise CoreError('VALIDATION_ERROR', 'CSV не содержит листов; уберите параметр sheet.')
            header, rows, _, truncated = read_csv(content)
            if truncated:
                raise CoreError('FILE_QUERY_INCOMPLETE', 'CSV превышает10000строк. Точный итог по частичной выборке не вычислялся.')
            return {'columns': header, 'rows': rows, 'sheet': None, 'row_start': 1 if rows else 0,
                'row_end': len(rows), 'total_rows': len(rows)}
        raise CoreError('FILE_QUERY_UNSUPPORTED', 'Точные табличные итоги поддерживаются для CSV/XLSX. Для других файлов используйте постраничное чтение.')

    def extract(self, content: bytes, filename: str, media_type: str) -> dict:
        name = filename.casefold()
        try:
            if name.endswith(('.xlsx','.docx')):
                if name.endswith('.xlsx'):
                    from bervel.core.infrastructure.office_reader import office_blocks
                    return office_blocks(content)
                from bervel.core.infrastructure.office_reader import office_text
                text=office_text(content, filename)
                result=self.extract(text.encode('utf-8'), 'office.txt', 'text/plain')
                if '[CONTENT_TRUNCATED]' in text: result['status']='partial'
                return result
            if content.startswith(b'%PDF-') or name.endswith('.pdf') or media_type == 'application/pdf':
                return self.pdf(content)
            if media_type.startswith('image/') or content.startswith((b'\x89PNG', b'\xff\xd8\xff', b'RIFF', b'GIF8', b'BM')):
                with TemporaryDirectory(prefix='bervel-image-') as folder:
                    path = Path(folder) / 'original'
                    path.write_bytes(content)
                    text = command(['tesseract', str(path), 'stdout', '-l', 'rus+eng']).strip()
                return {'status': 'ready', 'chunks': text_chunks(text or 'Изображение: распознаваемый текст отсутствует. Визуальное содержимое требует просмотра модели.', 'image', 1, 1)}
            if name.endswith('.csv') or media_type in {'text/csv', 'application/csv'}:
                header,rows,_,truncated=read_csv(content)
                kinds = [i for i, column in enumerate(header) if column.strip().casefold() == 'data_kind']
                mock = any(i < len(row) and row[i].strip().casefold() == 'mock' for row in rows for i in kinds)
                return {'status':'partial' if truncated else 'ready','chunks':table_blocks(header,rows), 'is_mock': mock}
            if b'\x00' not in content:
                text = content.decode('utf-8-sig')
                if not text.strip(): return unavailable('текстовый файл пуст.')
                if any(ord(char) < 32 and char not in '\r\n\t' for char in text):
                    return unavailable('бинарный формат; доступны только имя и свойства файла.')
                total=len(text.splitlines())
                chunks=split_blocks([dict(excerpt=text[:5000000],unit='line',row_start=1,row_end=total,total_rows=total)])
                return {'status':'partial' if len(text)>5000000 else 'ready','chunks':chunks}
            return unavailable('для этого формата ещё нет средства чтения.')
        except (CoreError, UnicodeError, ValueError, OSError, subprocess.SubprocessError):
            return unavailable('формат не поддерживается, файл повреждён, защищён или превышены ограничения чтения.')

    def pdf(self, content: bytes) -> dict:
        deadline = monotonic() + 60
        def run(args):
            return command(args, timeout=min(40, max(0.1, deadline - monotonic())))
        with TemporaryDirectory(prefix='bervel-pdf-') as folder:
            path = Path(folder) / 'original.pdf'; path.write_bytes(content)
            info = run(['pdfinfo', str(path)])
            match = re.search(r'^Pages:\s+(\d+)', info, re.MULTILINE)
            if not match: return unavailable('не удалось определить страницы PDF.')
            total = int(match[1])
            text = run(['pdftotext', '-f', '1', '-l', str(min(total, 100)), '-layout', '-enc', 'UTF-8', str(path), '-'])
            pages = text.split('\f')
            chunks, used, ocr, partial = [], 0, 0, total > 100
            for number in range(1, min(total, 100) + 1):
                page_text = pages[number - 1].strip() if number <= len(pages) else ''
                if not page_text:
                    if monotonic() >= deadline: partial = True; break
                    if ocr >= 10: partial = True; continue
                    ocr += 1
                    prefix = Path(folder) / f'page-{number}'
                    try:
                        run(['pdftoppm', '-f', str(number), '-l', str(number), '-scale-to', '1800', '-singlefile', '-png', str(path), str(prefix)])
                        page_text = run(['tesseract', str(prefix) + '.png', 'stdout', '-l', 'rus+eng']).strip()
                    except (ValueError, OSError, subprocess.SubprocessError):
                        partial = True; continue
                if not page_text: partial = True; continue
                if used + len(page_text) > 5000000: partial = True; break
                chunks.extend(text_chunks(page_text, 'page', number, total)); used += len(page_text)
            if not chunks: return unavailable('в PDF не удалось распознать текст; содержимое не подтверждено.')
            return {'status': 'partial' if partial else 'ready', 'chunks': chunks}
