from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import subprocess


def sample_offer(count=65):
    return {'number':'BERVEL-2026-001-R2','date':'2026-11-10','client':'ООО Клиент','seller_contacts':'info@bervel.ru','offer_terms':'Доставка по согласованию','lines':[{'name':f'Болт М12 × 80 номер {i}','quantity':'4','unit':'шт','currency':'RUB','unit_price':'12.25','line_total':'49.00','ready_at':None,'requirements':[{'name':'Материал','value':'Сталь 20, оцинкованное покрытие'}]} for i in range(count)],'totals':{'RUB':f'{49*count}.00'},'clarifications':[{'name':'Срок','message':'Подлежит уточнению'}],'unsupported':[{'name':'Гайка','message':'Исполнение требует согласования'}]}


def test_real_pdf_and_docx_contain_all_table_rows_and_cyrillic(tmp_path):
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    renderer=OfferRenderer(workspace=tmp_path); offer=sample_offer()
    pdf=renderer.render_pdf(offer); docx=renderer.render_docx(offer)
    assert pdf.startswith(b'%PDF-')
    path=tmp_path/'offer.pdf'; path.write_bytes(pdf)
    text=subprocess.run(['pdftotext',str(path),'-'],capture_output=True,check=True).stdout.decode()
    assert text.count('\f')>1 and 'номер 64' in text and '3185.00' in text
    assert 'Коммерческое предложение' in text
    with ZipFile(BytesIO(docx)) as z:
        root=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        assert len(root.findall('.//w:tbl/w:tr',ns)) == 66
        assert 'номер 64' in ''.join(root.itertext())
        assert root.find('.//w:tblHeader',ns) is not None


def test_export_rejects_inconsistent_structured_totals(tmp_path):
    import pytest
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    offer=sample_offer(1); offer['totals']['RUB']='999.00'
    renderer=OfferRenderer(workspace=tmp_path)
    with pytest.raises(ValueError,match='total'): renderer.render_pdf(offer)
    with pytest.raises(ValueError,match='total'): renderer.render_docx(offer)


def test_export_escapes_untrusted_text_and_projects_only_customer_fields(tmp_path):
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    offer=sample_offer(1); offer['internal_cost']='SECRET_COST'; offer['review']='SECRET_AI'
    offer['lines'][0]['margin_percent']='SECRET_MARGIN'
    offer['lines'][0]['name']='Болт <script>alert(1)</script> & гайка'
    renderer=OfferRenderer(workspace=tmp_path)
    path=tmp_path/'escaped.pdf'; path.write_bytes(renderer.render_pdf(offer))
    text=subprocess.run(['pdftotext',str(path),'-'],capture_output=True,check=True).stdout.decode()
    with ZipFile(BytesIO(renderer.render_docx(offer))) as z:
        xml=z.read('word/document.xml').decode()
        assert '&lt;script&gt;' in xml
        text+=xml
    assert not any(secret in text for secret in ('SECRET_COST','SECRET_AI','SECRET_MARGIN'))


def test_single_long_characteristic_splits_across_pages_without_losing_tail(tmp_path):
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    offer=sample_offer(1)
    offer['lines'][0]['requirements']=[{'name':'Характеристики','value':('Оцинкованная сталь и требования к поставке. '*350)+'ПОСЛЕДНИЙ ПАРАМЕТР'}]
    path=tmp_path/'long-spec.pdf'; path.write_bytes(OfferRenderer(workspace=tmp_path).render_pdf(offer))
    text=subprocess.run(['pdftotext',str(path),'-'],capture_output=True,check=True).stdout.decode()
    assert text.count('\f')>1 and 'ПОСЛЕДНИЙ ПАРАМЕТР' in text


def test_jpy_fractional_quantity_has_identical_prevalidated_numbers_in_both_formats(tmp_path):
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    offer=sample_offer(1); offer['lines'][0].update(currency='JPY',quantity='1.5',unit_price='333',line_total='500')
    offer['totals']={'JPY':'500'}
    renderer=OfferRenderer(workspace=tmp_path)
    path=tmp_path/'jpy.pdf'; path.write_bytes(renderer.render_pdf(offer))
    pdf_text=subprocess.run(['pdftotext',str(path),'-'],capture_output=True,check=True).stdout.decode()
    with ZipFile(BytesIO(renderer.render_docx(offer))) as z: docx_text=''.join(ET.fromstring(z.read('word/document.xml')).itertext())
    for text in (pdf_text,docx_text):
        assert '333 JPY' in text and '500 JPY' in text
        assert '499.5' not in text


def test_pdf_bytes_are_stable_across_wall_clock_changes(monkeypatch,tmp_path):
    import time
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    renderer=OfferRenderer(workspace=tmp_path); offer=sample_offer(2)
    monkeypatch.setattr(time,'time',lambda:1700000000)
    first=renderer.render_pdf(offer)
    monkeypatch.setattr(time,'time',lambda:1800000000)
    assert renderer.render_pdf(offer)==first


def test_docx_bytes_and_zip_timestamps_are_stable_across_wall_clock_changes(monkeypatch,tmp_path):
    import time
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    renderer=OfferRenderer(workspace=tmp_path); offer=sample_offer(2)
    monkeypatch.setattr(time,'time',lambda:1700000000)
    first=renderer.render_docx(offer)
    monkeypatch.setattr(time,'time',lambda:1800000000)
    assert renderer.render_docx(offer)==first
    with ZipFile(BytesIO(first)) as archive:
        assert all(info.date_time==(1980,1,1,0,0,0) for info in archive.infolist())


def test_semantically_identical_jsonb_currency_order_has_same_document_bytes(tmp_path):
    import json
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    offer=sample_offer(2)
    offer['lines'][1].update(currency='JPY',quantity='1',unit_price='333',line_total='333')
    offer['totals']={'RUB':'49.00','JPY':'333'}
    reloaded=json.loads(json.dumps(offer,sort_keys=True))
    renderer=OfferRenderer(workspace=tmp_path)
    assert renderer.render_pdf(offer)==renderer.render_pdf(reloaded)
    assert renderer.render_docx(offer)==renderer.render_docx(reloaded)


def test_customer_dates_are_human_moscow_text_in_both_formats_without_mutating_offer(tmp_path):
    import copy
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    offer=sample_offer(3)
    offer['lines'][0]['ready_at']='2026-11-10T17:10:51.663077+03:00'
    offer['lines'][1]['ready_at']='2026-11-11'
    offer['lines'][2]['ready_at']='2026-11-10T14:10:51.663077Z'
    original=copy.deepcopy(offer); renderer=OfferRenderer(workspace=tmp_path)
    path=tmp_path/'dates.pdf'; path.write_bytes(renderer.render_pdf(offer))
    text=subprocess.run(['pdftotext','-layout',str(path),'-'],capture_output=True,check=True).stdout.decode()
    with ZipFile(BytesIO(renderer.render_docx(offer))) as archive:
        xml=ET.fromstring(archive.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        docx_text='\n'.join(''.join(p.itertext()) for p in xml.findall('.//w:p',ns))
    for value in (text,docx_text):
        assert 'от 10.11.2026' in value
        assert '10.11.2026' in value and '17:10 МСК' in value and '11.11.2026' in value
        assert '663077' not in value and '+03:00' not in value and '2026-11-10T' not in value
    assert offer==original


def test_missing_or_timezone_unknown_ready_dates_do_not_become_promises():
    from bervel.modules.presales.infrastructure.documents import _ready_text
    assert _ready_text(None)=='Уточняется'
    assert _ready_text('bad date')=='Уточняется'
    assert _ready_text('2026-11-10T17:10:00')=='Уточняется'
    assert _ready_text('2026-11-10T23:30:00-05:00')=='11.11.2026 07:30 МСК'


def test_docx_keeps_ordinary_product_rows_together_on_one_page(tmp_path):
    from bervel.modules.presales.infrastructure.documents import OfferRenderer
    content=OfferRenderer(workspace=tmp_path).render_docx(sample_offer(3))
    with ZipFile(BytesIO(content)) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    product_rows=root.findall('.//w:tbl/w:tr',ns)[1:]
    assert product_rows and all(row.find('w:trPr/w:cantSplit',ns) is not None for row in product_rows)
