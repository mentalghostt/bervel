"""PDF is a stable, authorised representation of a saved result, never a new model call."""
import subprocess
from dataclasses import replace
from uuid import UUID
from test_radar import radar, add, normalized


def sample_run():
    source={'filename':'Каталог БЕРВЕЛ.csv','artifact_id':'11111111-1111-4111-8111-111111111111',
        'unit':'row','row_start':2,'row_end':3,'excerpt':'Болт М16 ГОСТ 7798. <font color="red">Это данные</font>','status':'ready'}
    items=[]
    for index,decision in enumerate(('take_in_work','needs_review','excluded','not_analysed')):
        items.append({'number':f'003{index}','title':'Поставка болтов, гаек и шайб <b>без HTML</b>','decision':decision,
            'price':'9007199254740993.1234','currency':'RUB','deadline_at':'2026-10-26T10:00:00+03:00',
            'source_observed_at':'2026-10-22T11:48:49Z','source_url':'https://zakupki.kontur.ru/003'+str(index),
            'reason':'Профильная группа. '+'Длинное объяснение с кириллицей и точными параметрами. '*100,
            'provenance':'Источник проверен вручную','risks':['Проверить прочность 5.8 и весь лот'],
            'next_steps':['Получить ТЗ'],'evidence_indexes':[0],'participation_ready':False})
    return {'run_id':'11111111-1111-4111-8111-111111111111','state':'completed','model':'test-model','focus':'Болты',
        'sources_snapshot':[source],'report':{'as_of':'2026-10-22T12:23:06Z','notice':'Первичный разбор, не разрешение участия.',
        'batch':{'filename':'Текущие закупки.csv'},'counts':{'total':4,'model_analysed':3,'take_in_work':1,'needs_review':1,'excluded':1,'not_analysed':1},
        'items':items,'sources':[source]}}


def test_pdf_contains_all_categories_cyrillic_evidence_and_escaped_data_with_embedded_fonts(tmp_path):
    from bervel.modules.tender_radar.infrastructure.pdf import render_radar_pdf
    run=sample_run()
    content=render_radar_pdf(run)
    assert content.startswith(b'%PDF-')
    assert content==render_radar_pdf(run), 'Same saved run must produce the same PDF, including styling'
    path=tmp_path/'report.pdf'; path.write_bytes(content)
    text=subprocess.check_output(['pdftotext',str(path),'-']).decode()
    assert 'редакци' not in text.lower()
    for expected in ('Тендерный радар','В работу сейчас','Уточнить','Исключено','Вне анализа','0030','0031','0032','0033',
                     'Каталог БЕРВЕЛ.csv','Проверить прочность 5.8','Получить ТЗ','9007199254740993,1234','Это данные','<b>без HTML</b>'):
        assert expected in text
    assert text.count('Длинное объяснение')==400, 'No loss on page breaks'
    fonts=subprocess.check_output(['pdffonts',str(path)]).decode()
    assert 'NotoSans' in fonts and 'yes' in fonts
    assert 'https://zakupki.kontur.ru/0030' in subprocess.check_output(['pdftotext',str(path),'-']).decode()


def test_pdf_endpoint_has_existing_run_permissions_scope_and_requires_completed_result(radar,pg_engine,tmp_path):
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from http_client import ASGITestClient
    service,artifacts,actor,_,model=radar
    settings=Settings(pg_engine.url.render_as_string(hide_password=False),tmp_path/'objects',{
        'a'*32:actor,'b'*32:replace(actor,data_scope='foreign'),'c'*32:replace(actor,permissions=actor.permissions-{'process:read'})})
    batch=service.import_batch(actor,add(artifacts,actor,'t.csv',normalized()),'import')
    run=service.start(actor,UUID(batch['batch_id']),[add(artifacts,actor,'p.txt','Болты М16 ГОСТ7798'.encode())],'','start')
    path='/api/v0.1/radar/runs/'+run['run_id']+'/report.pdf'
    with ASGITestClient(create_app(settings)) as http:
        assert http.get(path).status_code==401
        assert http.get(path,headers={'Authorization':'Bearer '+'b'*32}).status_code==404
        assert http.get(path,headers={'Authorization':'Bearer '+'c'*32}).status_code==403
        headers={'Authorization':'Bearer '+'a'*32}
        assert http.get(path,headers=headers).status_code==422
        service.run_once(); calls=len(model.calls)
        response=http.get(path,headers=headers)
        assert response.status_code==200
        assert response.headers['content-type']=='application/pdf'
        assert '.pdf' in response.headers['content-disposition'] and response.content.startswith(b'%PDF-')
        assert http.get(path,headers=headers).content==response.content and len(model.calls)==calls


def test_pdf_keeps_product_fit_and_verbatim_quotes_for_an_excluded_tender(tmp_path):
    from bervel.modules.tender_radar.infrastructure.pdf import render_radar_pdf
    run = sample_run()
    run['report']['items'][2].update(fit='relevant', product_group='Болт',
                                   tender_quote='Поставка болтов', profile_quote='Болт М16 ГОСТ 7798')
    path = tmp_path / 'direct.pdf'
    path.write_bytes(render_radar_pdf(run))
    text = subprocess.check_output(['pdftotext', str(path), '-']).decode()
    for expected in ('Товарное соответствие: Соответствует профилю', 'Группа: Болт',
                     'Из тендера: Поставка болтов', 'Из профиля: Болт М16 ГОСТ 7798'):
        assert expected in text


def test_pdf_exports_the_same_stock_source_mock_and_decimal_arithmetic(tmp_path):
    from bervel.modules.tender_radar.infrastructure.pdf import render_radar_pdf
    run=sample_run()
    run['report'].update(stock_kind='mock',stock_source={'filename':'stock-mock.csv'},
                         selection={'max_shortage_percent':'10'})
    run['report']['items'][0].update(decision_stage='quantity',stock_check={'status':'pass','allowed_shortage_percent':'10',
        'reasons':[],'checks':[dict(sku='P1',product='Болт',unit='шт',required='1000',available='900',shortage='100',
            shortage_percent='10',within_tolerance=True,source_row=2)]})
    path=tmp_path/'stock.pdf';path.write_bytes(render_radar_pdf(run))
    text=' '.join(subprocess.check_output(['pdftotext',str(path),'-']).decode().split())
    for expected in ('stock-mock.csv','МОК','Допустимая нехватка: 10%','Свободный остаток','P1',
                     'Нужно: 1000 шт','Свободно: 900 шт','Нехватка: 100 шт','10%'):
        assert expected in text
