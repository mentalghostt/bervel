from datetime import datetime, timezone
import io
from zipfile import ZipFile

import pytest
from bervel.core.errors import CoreError
from bervel.modules.tender_radar.infrastructure.importer import TenderImporter


def normalized(**changes):
    import csv
    row = dict(number='0337100015526000138', title='Болты М16 ГОСТ 7798', deadline_at='2026-10-26T10:00:00+03:00',
        source_url='https://zakupki.kontur.ru/0337100015526000138', price='15467.10', currency='RUB', stage='Этап подачи заявок',
        source_observed_at='2026-10-22T11:48:49Z', demo='false')
    row.update(changes)
    stream = io.StringIO(); writer = csv.DictWriter(stream, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
    return stream.getvalue().encode()


def test_csv_preserves_identity_price_timezone_and_unknowns():
    result = TenderImporter().parse(normalized(), 'tenders.csv')
    row = result['records'][0]
    assert row['number'] == '0337100015526000138' and row['price'] == '15467.10'
    assert datetime.fromisoformat(row['deadline_at']).utcoffset().total_seconds() == 10800
    assert row['stage'] == 'open' and row['demo'] is False
    assert row['origin']['row'] == 2


@pytest.mark.parametrize('changes', [{'deadline_at':'2026-10-26'}, {'source_url':'javascript:alert(1)'},
    {'price':'NaN'}, {'price':'1e100000'}, {'number':''}, {'source_observed_at':'tomorrow'}])
def test_rejects_invalid_rows_instead_of_silently_skipping(changes):
    with pytest.raises(CoreError): TenderImporter().parse(normalized(**changes), 't.csv')


def xlsx():
    # Minimal independent OOXML fixture: grouped duplicate headings and a hyperlink.
    import html
    rows = [[None], [None], ['Закупка'], ['Номер','ИКЗ','Название','НМЦ','Валюта закупки','Дата публикации','Окончание приема заявок','Тип торгов','Ссылка на ЕИС'],
        ['0033','0005','Поставка болтов','124.50','RUB','2026-10-20T09:00:00+03:00','2026-10-27T10:00:00+03:00','223-ФЗ','ЕИС'], ['Итого',None,None,'124.50']]
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetData>'
    for number, row in enumerate(rows,1):
        sheet += f'<row r="{number}">' + ''.join(f'<c r="{chr(65+i)}{number}" t="inlineStr"><is><t>{html.escape(value)}</t></is></c>' for i,value in enumerate(row) if value is not None) + '</row>'
    sheet += '</sheetData><hyperlinks><hyperlink ref="A5" r:id="link"/></hyperlinks></worksheet>'
    stream = io.BytesIO()
    with ZipFile(stream,'w') as z:
        z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Контур" sheetId="1" r:id="s"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',sheet)
        z.writestr('xl/worksheets/_rels/sheet1.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="link" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://zakupki.kontur.ru/0033" TargetMode="External"/></Relationships>')
    return stream.getvalue()


def test_native_kontur_xlsx_reads_hyperlinks_grouped_header_and_footer():
    result = TenderImporter().parse(xlsx(), '2.xlsx')
    assert len(result['records']) == 1 and result['skipped_rows'] == 1
    row = result['records'][0]
    assert row['number'] == '0033' and row['source_url'] == 'https://zakupki.kontur.ru/0033'
    assert row['stage'] == 'unknown' and row['source_observed_at'] is None
    assert row['origin'] == {'sheet': 'Контур', 'row':5}


def test_conflicting_duplicate_is_visible_and_cannot_be_treated_as_clean():
    header, first = normalized().decode().splitlines()
    _, second = normalized(deadline_at='2026-10-30T10:00:00+03:00').decode().splitlines()
    result = TenderImporter().parse((header+'\n'+first+'\n'+second+'\n').encode(), 'd.csv')
    assert len(result['records']) == 2
    assert all(row['identity_conflict'] for row in result['records'])


@pytest.mark.parametrize('raw',['Приём заявок окончен','Подача заявок приостановлена','Приём заявок не начат'])
def test_negative_stage_phrases_cannot_open_a_purchase(raw):
    assert TenderImporter().parse(normalized(stage=raw),'t.csv')['records'][0]['stage']!='open'


@pytest.mark.parametrize('changes',[{'currency':'USD'},{'source_observed_at':'2026-10-22T11:00:00Z'},{'demo':'true'}])
def test_material_duplicate_differences_are_never_silently_lost(changes):
    header,first=normalized().decode().splitlines()
    _,second=normalized(**changes).decode().splitlines()
    result=TenderImporter().parse((header+'\n'+first+'\n'+second+'\n').encode(),'t.csv')
    assert len(result['records'])==2 and all(row['identity_conflict'] for row in result['records'])


def test_excel_numeric_identity_uses_zero_format_and_rejects_precision_loss():
    from bervel.modules.tender_radar.infrastructure.importer import excel_identifier
    from types import SimpleNamespace
    assert excel_identifier(SimpleNamespace(value=33,number_format='0000'))=='0033'
    with pytest.raises(CoreError): excel_identifier(SimpleNamespace(value=123456789012345678,number_format='0'))
