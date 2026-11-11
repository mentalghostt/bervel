"""Bounded CSV decoding and evidence chunks, independent of business schemas."""
import csv
import io
import json
import sys

from bervel.core.errors import CoreError

csv.field_size_limit(sys.maxsize)
CHUNK_CHARS = 3500
MAX_ROWS = 10000
MAX_COLUMNS = 200


def decode_text(content: bytes) -> tuple[str, str]:
    if content.startswith((b'\xff\xfe', b'\xfe\xff')):
        return content.decode('utf-16'), 'UTF-16'
    try:
        return content.decode('utf-8-sig'), 'UTF-8'
    except UnicodeError:
        return content.decode('cp1251'), 'Windows-1251'


def read_csv(content: bytes, *, max_rows: int | None = MAX_ROWS) -> tuple[list[str], list[list[str]], str, bool]:
    """Return exact cells and whether rows were capped; never infer numeric types."""
    try:
        if not content:
            raise ValueError('CSV requires content')
        decoded, encoding = decode_text(content)
        if any(ord(char) < 32 and char not in '\r\n\t' for char in decoded):
            raise ValueError('CSV contains binary control characters')
        dialect = csv.excel
        # A cut inside a quoted multiline cell can
        # look like a different delimiter, so inspect the complete CSV first.
        for sample in (decoded, decoded.splitlines()[0] if decoded else ''):
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
                break
            except csv.Error:
                continue
        reader = csv.reader(io.StringIO(decoded, newline=''), dialect, strict=True)
        header = next(reader)
        if not header or len(header) > MAX_COLUMNS:
            raise ValueError('CSV requires 1..200 columns')
        rows = []
        for row in reader:
            if len(row) > MAX_COLUMNS:
                raise ValueError('CSV exceeds 200 columns')
            if max_rows is not None and len(rows) == max_rows:
                return header, rows, encoding, True
            rows.append(row)
        return header, rows, encoding, False
    except (UnicodeError, csv.Error, ValueError, StopIteration) as error:
        raise CoreError('VALIDATION_ERROR', 'CSV is invalid or exceeds decoding limits; original is preserved') from error


def parse_csv(content: bytes) -> list[dict]:
    try:
        header, rows, _, truncated = read_csv(content)
        header_text = 'Columns: ' + json.dumps(header, ensure_ascii=False)
        chunks: list[dict] = []
        prefix = header_text + '\n' if len(header_text) < 1000 else ''
        current = prefix
        start = end = total = 0

        def append(text: str, first: int, last: int) -> None:
            chunks.append({'ordinal': len(chunks), 'row_start': first, 'row_end': last, 'excerpt': text})

        if not prefix:
            for position in range(0, len(header_text), CHUNK_CHARS):
                append(header_text[position:position + CHUNK_CHARS], 0, 0)
        for total, row in enumerate(rows, 1):
            line = f'Row {total}: ' + json.dumps(row, ensure_ascii=False) + '\n'
            if len(current) + len(line) > CHUNK_CHARS and end:
                append(current, start, end)
                current, start, end = prefix, 0, 0
            if len(prefix) + len(line) > CHUNK_CHARS:
                width = CHUNK_CHARS - len(prefix)
                for position in range(0, len(line), width):
                    append(prefix + line[position:position + width], total, total)
            else:
                current += line
                start = start or total
                end = total
        if end or not chunks:
            append(current, start, end)
        return [{**chunk, 'total_rows': total, 'truncated': truncated} for chunk in chunks]
    except (UnicodeError, csv.Error, ValueError, StopIteration) as error:
        raise CoreError('VALIDATION_ERROR', 'CSV could not be converted to evidence chunks') from error
