"""Local, bounded OOXML readers; no macros, formulas, URLs or embedded objects run."""
import io
from zipfile import ZipFile, BadZipFile
from xml.etree.ElementTree import ParseError
from openpyxl.utils.exceptions import InvalidFileException
from defusedxml.ElementTree import fromstring
from defusedxml.common import DefusedXmlException
from bervel.core.errors import CoreError
from bervel.core.chunking import split_blocks


def bounded_zip(content: bytes) -> None:
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            entries=archive.infolist()
            if len(entries)>1000 or sum(item.file_size for item in entries)>50*1024*1024:
                raise CoreError('VALIDATION_ERROR','Архив превышает50МиБ /1000частей.')
            if any(item.flag_bits & 1 for item in entries):
                raise CoreError('VALIDATION_ERROR','Зашифрованный архив не поддерживается.')
    except BadZipFile as error:
        raise CoreError('VALIDATION_ERROR','Повреждённый XLSX/DOCX.') from error


def office_table(content: bytes, *, sheet: str | None = None) -> dict:
    """Read one exact worksheet without evaluating formulas or using stale cached values."""
    bounded_zip(content)
    import openpyxl
    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
    try:
        if sheet is None and len(book.sheetnames) != 1:
            raise CoreError('FILE_QUERY_SHEET_REQUIRED', 'Выберите точное имя листа XLSX: '+', '.join(book.sheetnames))
        if sheet is not None and sheet not in book.sheetnames:
            raise CoreError('VALIDATION_ERROR', 'Указанный лист XLSX не найден.')
        selected = book[sheet or book.sheetnames[0]]
        if (selected.max_row or 0) > 10001 or (selected.max_column or 0) > 200:
            raise CoreError('FILE_QUERY_INCOMPLETE', 'Лист превышает10000строк данных /200столбцов; точный итог не вычислялся.')
        # XLSX producers sometimes write a smaller dimension hint than the actual XML rows.
        selected.reset_dimensions()
        header, rows, size, first_row, last_row = None, [], 0, 0, 0
        for number, cells in enumerate(selected, 1):
            if number > 10001 or len(cells) > 200:
                raise CoreError('FILE_QUERY_INCOMPLETE', 'Лист превышает ограничения чтения; точный итог не вычислялся.')
            values = [({'unsupported': 'formula'} if cell.data_type in {'f', 'e'} else cell.value) for cell in cells]
            if not any(value is not None for value in values): continue
            size += sum(len(str(value)) for value in values)
            if size > 5000000:
                raise CoreError('FILE_QUERY_INCOMPLETE', 'Текст ячеек превышает5МиБ; точный итог не вычислялся.')
            if header is None:
                if any(isinstance(value, dict) for value in values):
                    raise CoreError('VALIDATION_ERROR', 'Заголовок таблицы содержит формулу или ошибку.')
                header = [str(value) if value is not None else '' for value in values]
            else:
                values.extend([None] * max(0, len(header)-len(values)))
                rows.append(values)
                first_row, last_row = first_row or number, number
        if header is None:
            raise CoreError('VALIDATION_ERROR', 'В выбранном листе нет заголовка таблицы.')
        return {'columns': header, 'rows': rows, 'sheet': selected.title,
            'row_start': first_row, 'row_end': last_row, 'total_rows': last_row}
    except (ValueError, KeyError, BadZipFile, DefusedXmlException, ParseError, InvalidFileException) as error:
        raise CoreError('VALIDATION_ERROR', 'Не удалось безопасно прочитать таблицу XLSX.') from error
    finally:
        book.close()


def office_text(content: bytes, filename: str) -> str:
    bounded_zip(content)
    try:
        if filename.casefold().endswith('.docx'):
            with ZipFile(io.BytesIO(content)) as archive:
                root=fromstring(archive.read('word/document.xml'))
            ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            paragraphs=[]; size=0; partial=False
            for p in root.iter(ns+'p'):
                text=''.join(n.text or '' for n in p.iter(ns+'t'))
                paragraphs.append(text[:10000]); size+=len(text)+1
                partial=partial or len(text)>10000
                if size>5000000: partial=True; break
            return '\n'.join(paragraphs)+ ('\n[CONTENT_TRUNCATED]' if partial else '')
        import openpyxl
        book=openpyxl.load_workbook(io.BytesIO(content),read_only=True,data_only=False,keep_links=False)
        lines=[]; size=0; partial=False
        try:
            for sheet in book:
                if (sheet.max_row or 0)>10000 or (sheet.max_column or 0)>200:
                    raise CoreError('VALIDATION_ERROR','Слишком большой лист для чтения.')
                for number,row in enumerate(sheet,1):
                    if number>10000 or len(row)>200: raise CoreError('VALIDATION_ERROR','Слишком большой лист для чтения.')
                    values=[f'{cell.column_letter}={cell.value}' for cell in row if cell.value is not None and cell.data_type!='f']
                    if not values: continue
                    line=f'Sheet {sheet.title}, row {number}: '+' | '.join(values)
                    lines.append(line[:10000]); size+=len(line)+1
                    partial=partial or len(line)>10000
                    if size>200000: return '\n'.join(lines)+'\n[CONTENT_TRUNCATED]'
            return '\n'.join(lines)+ ('\n[CONTENT_TRUNCATED]' if partial else '')
        finally: book.close()
    except (ValueError,KeyError,BadZipFile,DefusedXmlException,ParseError,InvalidFileException) as error:
        raise CoreError('VALIDATION_ERROR','Не удалось безопасно прочитать офисный файл.') from error


def office_blocks(content:bytes)->dict:
    """Keep worksheet row identity and column names beside each value."""
    bounded_zip(content)
    import openpyxl
    book=openpyxl.load_workbook(io.BytesIO(content),read_only=True,data_only=False,keep_links=False)
    blocks,size,partial,mock=[],0,False,False
    try:
        for sheet in book:
            if (sheet.max_row or 0)>10000 or (sheet.max_column or 0)>200:
                raise CoreError('VALIDATION_ERROR','Слишком большой лист для чтения.')
            header={}
            for number,row in enumerate(sheet,1):
                values={cell.column_letter:str(cell.value) for cell in row if cell.value is not None and cell.data_type!='f'}
                if not values: continue
                if not header or any(v.casefold()=='номер' for v in values.values()): header=values
                mock = mock or any(header.get(k, '').strip().casefold() == 'data_kind' and v.strip().casefold() == 'mock'
                    for k, v in values.items())
                identity=' | '.join(f'{header.get(k,k)}: {v[:120]}' for k,v in list(values.items())[:2])
                context=f'Sheet {sheet.title}, row {number}\nКонтекст строки: {identity}\n'
                cells=[(f'{header.get(k,k)} ({k}): ',v) for k,v in values.items()]
                size+=sum(len(k)+len(v) for k,v in cells)
                if size>5000000: partial=True; break
                short='\n'.join(k+v for k,v in cells if len(v)<=800)
                pending=[(context,short)] if short else []
                pending.extend((context+k,v) for k,v in cells if len(v)>800)
                for prefix,body in pending:
                    blocks.extend(split_blocks([dict(excerpt=prefix+body,context_prefix=prefix,unit='row',row_start=number,row_end=number,total_rows=sheet.max_row or number)]))
            if partial: break
        return {'status':'partial' if partial else 'ready','chunks':blocks or [dict(excerpt='Пустая таблица.',unit='row',row_start=0,row_end=0,total_rows=0)], 'is_mock': mock}
    finally: book.close()
