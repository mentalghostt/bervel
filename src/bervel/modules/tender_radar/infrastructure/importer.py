"""Bounded XLSX/CSV transport decoding; never executes formulas or follows links."""
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import io
import re
from urllib.parse import urlsplit
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError
from defusedxml.common import DefusedXmlException
from openpyxl.utils.exceptions import InvalidFileException

from bervel.core.errors import CoreError
from bervel.core.infrastructure.office_reader import bounded_zip

MSK = timezone(timedelta(hours=3))


def fail(message):
    raise CoreError('VALIDATION_ERROR', message)


def date(value, *, allow_naive=False):
    if value is None or str(value).strip() == '': return None
    if isinstance(value, datetime): parsed = value
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace('Z','+00:00'))
        except ValueError:
            if not allow_naive: fail('Дата должна содержать время и часовой пояс ISO8601.')
            try: parsed = datetime.strptime(raw, '%d.%m.%Y %H:%M')
            except ValueError: fail('Не удалось прочитать дату Excel.')
        if not re.search(r'[T ]\d\d:\d\d', raw) and not (allow_naive and isinstance(value, datetime)):
            fail('Дата должна содержать время, а не только календарный день.')
    if parsed.tzinfo is None:
        if not allow_naive: fail('Укажите часовой пояс даты.')
        parsed = parsed.replace(tzinfo=MSK)
    return parsed.isoformat()


def url(value):
    value = str(value or '').strip()
    parts = urlsplit(value)
    if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password or len(value)>2000:
        fail('Нужна полная HTTP(S) ссылка на источник закупки.')
    return value


def stage(value):
    raw = ' '.join(str(value or '').casefold().replace('ё','е').split())
    if re.search('отмен|cancel',raw): return 'cancelled'
    if re.search('заверш|подвед|closed|finished|заключ|окончен|прекращ|закрыт',raw): return 'closed'
    if re.search('приостанов|не нач|ожида|suspend|paused|not started',raw): return 'unknown'
    if raw in {'этап подачи заявок','прием заявок','подача заявок','прием заявок продолжается','открыт прием заявок','open','active'}: return 'open'
    return 'unknown'


def price(value):
    if value is None or str(value).strip() in {'','—','-'}: return None
    raw = str(value).replace('\xa0','').replace(' ','').replace(',','.')
    try: number = Decimal(raw)
    except InvalidOperation: fail('Цена должна быть числом или пустой.')
    if not number.is_finite() or number < 0: fail('Цена должна быть конечной и неотрицательной.')
    digits=number.as_tuple()
    if not -12<=digits.exponent<=18 or len(digits.digits)>30 or number.adjusted()>18:
        fail('Цена превышает допустимую точность или разрядность.')
    return format(number,'f')


def excel_identifier(cell):
    value=cell.value
    if isinstance(value,(int,float)):
        if not float(value).is_integer() or len(str(abs(int(value))))>15:
            fail('Числовой номер закупки мог потерять точность Excel; сохраните идентификатор текстом.')
        if re.fullmatch(r'0{1,128}',cell.number_format or ''):
            return str(int(value)).zfill(len(cell.number_format))
        return str(int(value))
    return str(value or '').strip()


class TenderImporter:
    def parse(self, content: bytes, filename: str) -> dict:
        if not content or len(content)>5*1024*1024: fail('Нужен непустой файл до5МиБ.')
        try:
            if filename.casefold().endswith('.xlsx'):
                rows, skipped = self._excel(content)
            elif filename.casefold().endswith('.csv'):
                rows, skipped = self._csv(content)
            else: fail('Для списка тендеров нужен XLSX Контур или UTF-8 CSV.')
        except CoreError: raise
        except (ValueError, KeyError, UnicodeError, csv.Error, BadZipFile, OSError, ParseError, DefusedXmlException, InvalidFileException) as error:
            raise CoreError('VALIDATION_ERROR','Не удалось прочитать таблицу тендеров; проверьте формат и строки.') from error
        if not rows: fail('В файле нет карточек закупок.')
        if len(rows)>10000: fail('В одной партии допускается до10000 закупок.')
        groups = defaultdict(list)
        for row in rows: groups[(row['number'],row['source_url'])].append(row)
        unique=[]; duplicates=0
        for group in groups.values():
            variants={}
            for row in group:
                key=json.dumps({key:value for key,value in row.items() if key not in {'id','origin','identity_conflict'}},sort_keys=True,ensure_ascii=False)
                if key in variants:
                    duplicates+=1
                    variants[key]['origins'].append(row['origin'])
                    continue
                row['origins']=[row['origin']]
                variants[key]=row
            for row in variants.values():
                row['identity_conflict']=len(variants)>1
                unique.append(row)
        return {'schema_id':'bervel.tender-radar.import','schema_version':'1.0.0','records':unique,'skipped_rows':skipped,'duplicate_rows':duplicates,
                'warnings':['Импорт — снимок. Документация и текущая стадия требуют отдельной проверки. Даты Excel без пояса трактуются как МСК.']}

    def _record(self, values, origin, *, excel=False):
        if any(len(str(value))>10000 for value in values.values()): fail(f'Слишком длинное значение, строка {origin["row"]}.')
        number=str(values.get('number') or '').strip()
        title=str(values.get('title') or '').strip()
        if not number or len(number)>200 or not title: fail(f'Нужны номер и название, строка {origin["row"]}.')
        try:
            link=url(values.get('source_url'))
            deadline=date(values.get('deadline_at'),allow_naive=excel)
            published=date(values.get('published_at'),allow_naive=excel)
            observed=date(values.get('source_observed_at'))
            amount=price(values.get('price'))
        except CoreError as error: fail(f'Строка {origin["row"]}: {error.message}')
        return {'id':hashlib.sha256((number+'\n'+link+'\n'+str(origin)).encode()).hexdigest()[:32],
            'number':number,'title':title,'buyer':str(values.get('buyer') or '')[:1000],
            'price':amount,'currency':str(values.get('currency') or '')[:16], 'published_at':published,
            'deadline_at':deadline,'source_url':link,'source_observed_at':observed,
            'stage':stage(values.get('stage')), 'stage_original':str(values.get('stage') or '')[:300],
            'region':str(values.get('region') or '')[:1000], 'description':str(values.get('description') or ''),
            'provenance':str(values.get('provenance') or 'Импортированный файл; полнота документации не подтверждена.')[:4000],
            'demo':str(values.get('demo','false')).casefold() in {'true','1','да'}, 'origin':origin,'identity_conflict':False}

    def _csv(self, content):
        text=content.decode('utf-8-sig')
        if '\x00' in text: fail('CSV содержит нулевой байт.')
        try: dialect=csv.Sniffer().sniff(text[:10000],delimiters=',;\t')
        except csv.Error: dialect=csv.excel
        reader=csv.DictReader(io.StringIO(text,newline=''),dialect=dialect,strict=True)
        if not {'number','title','deadline_at','source_url'} <= set(reader.fieldnames or []):
            fail('CSV: нужны колонки number,title,deadline_at,source_url; остальные поля необязательны.')
        result=[]; skipped=0
        for row in reader:
            if None in row: fail(f'Лишние значения CSV, строка {reader.line_num}.')
            if not any(row.values()): skipped+=1; continue
            if len(result)>=10000: fail('В одной партии допускается до10000 закупок.')
            result.append(self._record(row,{'sheet':None,'row':reader.line_num}))
        return result,skipped

    def _excel(self, content):
        import openpyxl
        bounded_zip(content)
        book=openpyxl.load_workbook(io.BytesIO(content),data_only=False,keep_links=False)
        result=[]; skipped=0
        try:
            # Multi-template exports have a consolidated sheet; do not import it twice.
            sheets=[book['Все шаблоны']] if 'Все шаблоны' in book.sheetnames else list(book)
            for sheet in sheets:
                if sheet.max_row>10050 or sheet.max_column>200: fail('Размер листа превышает10000 строк /200 колонок.')
                header=None
                for row in sheet.iter_rows(max_row=min(50,sheet.max_row)):
                    names=[str(c.value or '').strip().casefold() for c in row]
                    if 'номер' in names and 'название' in names and 'окончание приема заявок' in names:
                        header=row[0].row
                        break
                if header is None: continue
                def col(label, offset=0):
                    found=[i for i,name in enumerate(names) if name==label]
                    return found[offset] if len(found)>offset else None
                columns={'number':col('номер'),'title':col('название'),'price':col('нмц'),'currency':col('валюта закупки'),
                    'deadline_at':col('окончание приема заявок'),'published_at':col('дата публикации'),
                    'buyer':col('название',1),'region':col('регион'),'stage':col('этап')}
                for cells in sheet.iter_rows(min_row=header+1):
                    number=cells[columns['number']].value
                    if number is None and cells[columns['title']].value:
                        fail(f'Содержательная строка без номера закупки, строка {cells[0].row}.')
                    if number is None or re.match(r'^(итого|всего)',str(number).strip(),re.I): skipped+=1; continue
                    if any(c.data_type=='f' for c in cells[:min(24,len(cells))]): fail(f'Формулы в данных закупки, строка {cells[0].row}.')
                    values={key:cells[index].value if index is not None else None for key,index in columns.items()}
                    cell=cells[columns['number']]
                    values['number']=excel_identifier(cell)
                    # Source link on the purchase number owns identity; EIS search links are secondary.
                    if cell.hyperlink: values['source_url']=cell.hyperlink.target
                    elif col('ссылка на еис') is not None:
                        linked=cells[col('ссылка на еис')]
                        values['source_url']=linked.hyperlink.target if linked.hyperlink else linked.value
                    result.append(self._record(values,{'sheet':sheet.title,'row':cells[0].row},excel=True))
            return result,skipped
        finally: book.close()
