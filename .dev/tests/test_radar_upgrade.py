from datetime import datetime,timezone
from decimal import Decimal
import pytest
from test_radar_analysis import tender,assessment,SOURCE,NOW
from test_radar_stock import position
from bervel.modules.tender_radar.analysis import make_report
from bervel.modules.tender_radar.infrastructure.importer import TenderImporter
from test_tender_importer import normalized


def test_duplicate_rows_retain_both_origins():
    content=normalized(); content+=content.splitlines(keepends=True)[1]
    data=TenderImporter().parse(content,'t.csv')
    assert len(data['records'])==1
    assert data['duplicate_rows']==1
    assert [o['row'] for o in data['records'][0]['origins']]==[2,3]


def test_nonzero_allowed_shortage_still_requires_review():
    stock={'kind':'reported','items':[{'sku':'P1','product':'Болт','free_quantity':'900','unit':'шт','specs':{},'row':2,'as_of':None}]}
    row=tender(title='Болт М16: 1000 шт')
    result=make_report([row],[SOURCE],[assessment(positions=[position()],positions_complete=True)],NOW,stock=stock,allowed=Decimal('10'))
    assert result['items'][0]['decision']=='needs_review'


def test_economics_uses_sales_not_budget_and_margin_boundary():
    from bervel.modules.tender_radar.economics import economics_check
    row=tender(price='1000',description='Поставка в течение 10 дней')
    stock={'status':'pass','checks':[{'sku':'P1','required':'10','shortage':'0','unit':'шт'}]}
    data={'kind':'mock','items':[{'sku':'P1','unit':'шт','currency':'RUB','sale_price':'10','unit_cost':'8','fixed_cost':'2','logistics_cost':'2','packaging_per_unit':'0.1','capacity_per_day':'100','production_lead_days':'0','delivery_lead_days':'1','row':2}]}
    result=economics_check(row,stock,data,Decimal('15'),NOW)
    assert result['revenue']=='100' and result['total_cost']=='85'
    assert result['margin_percent']=='15' and result['status']=='pass'
    assert economics_check(row,stock,data,Decimal('15.01'),NOW)['status']=='fail'
    assert economics_check(row|{'price':'99'},stock,data,Decimal('15'),NOW)['status']=='fail'


def test_special_hrc_assembly_cannot_match_ordinary_bolt():
    from bervel.modules.tender_radar.stock import stock_check
    stock={'kind':'reported','items':[{'sku':'P1','product':'Болт шестигранный','free_quantity':'9999','unit':'комплект','specs':{'thread':'M27','length_mm':'90','strength':'10.9'},'row':2,'as_of':None}]}
    p=position(quantity='1',unit='комплект',quote='Болтокомплект M27x90 EN 14399-10 HRC 10.9: 1 комплект')
    assert stock_check([p],True,stock,Decimal(0))['status']=='unknown'
from bervel.modules.tender_radar.service import RadarService


def test_analyse_pins_three_roles_and_filters_in_command(radar,monkeypatch):
    from test_radar import add
    service,artifacts,actor,repo,_=radar
    refs=[add(artifacts,actor,n,c) for n,c in [('p.csv',normalized()),('s.csv',b'sku,product,free_quantity,unit\nP1,bolt,1,pcs\n'),('e.csv',b'CSV')]]
    roles=dict(zip([r.artifact_id for r in refs],['procurement','stock','economics']))
    def prepared(a,r):return artifacts.metadata(a,r)|{'usage_role':roles.get(r.artifact_id, artifacts.metadata(a,r)['usage_role']),'processing_state':'ready'}
    monkeypatch.setattr(artifacts,'prepared_metadata',prepared,raising=False)
    run=service.analyse(actor,*refs,{'min_price':'0','min_margin_percent':'15'},'analyse')
    assert len(run['sources_snapshot'])==3
    assert run['selection']['filters']['min_margin_percent']=='15'
    assert service.analyse(actor,*refs,{'min_price':'0.00','min_margin_percent':'15.0'},'analyse')==run
    with pytest.raises(Exception):service.analyse(actor,*refs,{'min_price':'1'},'analyse')
    roles[refs[0].artifact_id]='general'
    with pytest.raises(Exception):service.analyse(actor,*refs,{},'wrong')


def test_filters_exclude_price_and_days_before_semantics():
    from bervel.modules.tender_radar.full import filter_rows,validate_filters
    rows=[tender('cheap',price='9'),tender('soon',price='20',deadline_at='2026-10-23T12:00:00Z'),tender('valid',price='30')]
    accepted,excluded=filter_rows(rows,[SOURCE],NOW,validate_filters({'min_price':'10','min_days_remaining':2}))
    assert [r['id'] for r in accepted]==['valid']
    assert set(excluded)=={'cheap','soon'}

from test_radar import radar


def full_setup(radar,monkeypatch,*,description='Болт ГОСТ7798 M16x50 8.8 цинк: 10 шт. Поставка в течение 10 дней',stock_count='10',model=None):
    from test_radar import add
    from bervel.modules.tender_radar.infrastructure.stock import StockImporter
    from bervel.modules.tender_radar.infrastructure.economics import EconomicsImporter
    service,artifacts,actor,repo,_=radar
    service.stock_importer=StockImporter();service.economics_importer=EconomicsImporter()
    stock=b'sku,product,free_quantity,unit,standard,thread,length_mm,strength,coating,data_kind\nP1,Bolt,'+stock_count.encode()+',шт,ГОСТ7798,M16,50,8.8,цинк,mock\n'.encode()
    econ=b'sku,unit,currency,sale_price,unit_cost,fixed_cost,logistics_cost,packaging_per_unit,capacity_per_day,production_lead_days,delivery_lead_days,data_kind\n'+ 'P1,шт,RUB,10,8,2,2,0.1,100,0,1,mock\n'.encode()
    refs=[add(artifacts,actor,n,c) for n,c in [('p.csv',normalized(title=description,price='1000')),('s.csv',stock),('e.csv',econ)]]
    roles=dict(zip([r.artifact_id for r in refs],['procurement','stock','economics']))
    def prepared(a,r):return artifacts.metadata(a,r)|{'usage_role':roles.get(r.artifact_id, artifacts.metadata(a,r)['usage_role']),'processing_state':'ready'}
    monkeypatch.setattr(artifacts,'prepared_metadata',prepared,raising=False)
    class FullModel:
        def generate(self,key,prompt,**kwargs):
            import json
            data=json.loads(prompt.split('\n',1)[1]);values=[]
            for r in data['TENDERS']:
                values.append(assessment(r['id'],tender_quote='Болт',profile_quote='Bolt',product_group='Bolt',positions=[position(quantity='10',quote='Болт ГОСТ7798 M16x50 8.8 цинк: 10 шт')],positions_complete=True))
            return {'model':'fixture','answer':json.dumps({'items':values})}
    service.model=model or FullModel()
    run=service.analyse(actor,*refs,{},'full')
    return service,actor,run,refs


def test_full_worker_economics_and_mock_api_contract(radar,monkeypatch):
    from uuid import UUID
    from bervel.modules.tender_radar.api import RunResponse
    service,actor,run,refs=full_setup(radar,monkeypatch)
    assert service.run_once()
    result=service.read(actor,UUID(run['run_id']))
    assert result['state']=='completed',result['error']
    report=result['report'];item=report['items'][0]
    assert item['decision']=='take_in_work',item
    assert item['mock'] and not item['participation_ready']
    assert item['economics_check']['revenue']=='100'
    assert report['coverage']['accounted_rows']==1
    assert RunResponse.model_validate(result).report.schema_version=='1.3.0'


def test_missing_delivery_never_claims_participation(radar,monkeypatch):
    from uuid import UUID
    service,actor,run,_=full_setup(radar,monkeypatch,description='Болт ГОСТ7798 M16x50 8.8 цинк: 10 шт')
    service.run_once();item=service.read(actor,UUID(run['run_id']))['report']['items'][0]
    assert item['decision']=='needs_review'
    assert item['economics_check']['status']=='unknown'


def test_portion_failure_is_saved_explicitly_and_not_completed(radar,monkeypatch):
    from uuid import UUID
    class Broken:
        def generate(self,*args,**kwargs):raise RuntimeError('provider disconnected')
    service,actor,run,_=full_setup(radar,monkeypatch,model=Broken())
    service.run_once();result=service.read(actor,UUID(run['run_id']))
    assert result['state']=='failed'
    assert result['report']['items'][0]['decision']=='not_analysed'
    assert result['report']['coverage']['model_failed_rows']==1
    assert not result['report']['analysis_complete']


def test_new_pdf_has_real_economics_and_combined_exclusions(radar,monkeypatch,tmp_path):
    from uuid import UUID
    import subprocess
    from bervel.modules.tender_radar.infrastructure.pdf import render_radar_pdf
    service,actor,run,_=full_setup(radar,monkeypatch)
    service.run_once();result=service.read(actor,UUID(run['run_id']))
    path=tmp_path/'full.pdf';path.write_bytes(render_radar_pdf(result))
    text=' '.join(subprocess.check_output(['pdftotext',str(path),'-']).decode().split())
    for expected in ('Можно участвовать','Выручка по ценам продажи: 100 RUB','Полные расходы: 85 RUB','Маржа: 15%','МОК'):
        assert expected in text

def test_empty_catalogue_characteristics_require_review(radar,monkeypatch):
    from uuid import UUID
    service,actor,run,_=full_setup(radar,monkeypatch)
    parse=service.stock_importer.parse
    def empty_specs(*args):
        data=parse(*args);data['items'][0]['specs']={};return data
    monkeypatch.setattr(service.stock_importer,'parse',empty_specs)
    service.run_once();item=service.read(actor,UUID(run['run_id']))['report']['items'][0]
    assert item['decision']=='needs_review'
    assert item['technical_readiness']=='unknown'


def test_real_shared_index_readiness_and_full_worker(radar,monkeypatch,services,pg_engine):
    from uuid import UUID
    from test_knowledge import setup
    from bervel.core.document_memory import DocumentMemory
    service,actor,run,refs=full_setup(radar,monkeypatch)
    monkeypatch.undo()
    for ref,role in zip(refs,('procurement','stock','economics')):service.artifacts.set_usage_role(actor,ref.artifact_id,role)
    with pytest.raises(Exception,match='FILE_NOT_READY'):service.analyse(actor,*refs,{},'notready')
    knowledge,repo,_,_,_=setup(services,pg_engine)
    while knowledge.run_once():pass
    assert all(service.artifacts.prepared_metadata(actor,r)['processing_state']=='ready' for r in refs)
    service.memory=DocumentMemory(service.artifacts,repo,knowledge.reader,knowledge=knowledge)
    # Reuse the originally queued refs after actual indexing, with no readiness double.
    service.run_once();result=service.read(actor,UUID(run['run_id']))
    assert result['state']=='completed',result['error']
    assert result['report']['items'][0]['economics_check']['margin_percent']=='15'

@pytest.mark.parametrize('change,expected',[
    ({'currency':'EUR'},'unknown'),
    ({'description':''},'unknown'),
    ({'price':'99'},'fail'),
    ({'description':'Поставка в течение 0 дней'},'fail'),
])
def test_economics_currency_delivery_and_budget_limits(change,expected):
    from bervel.modules.tender_radar.economics import economics_check
    row=tender(price='1000',description='Поставка в течение 10 дней')|change
    stock={'status':'pass','checks':[{'sku':'P1','required':'10','shortage':'0','unit':'шт'}]}
    data={'kind':'reported','items':[{'sku':'P1','unit':'шт','currency':'RUB','sale_price':'10','unit_cost':'8','fixed_cost':'2','logistics_cost':'2','packaging_per_unit':'0.1','capacity_per_day':'100','production_lead_days':'0','delivery_lead_days':'1','row':2}]}
    assert economics_check(row,stock,data,Decimal('15'),NOW)['status']==expected


def test_interrupt_retains_last_report_checkpoint_without_repeating_model(radar,monkeypatch):
    from uuid import UUID
    service,actor,run,_=full_setup(radar,monkeypatch)
    # Simulate process stopping after a durable report checkpoint was committed.
    with service.repository.claim() as claimed:
        service.repository.checkpoint(claimed['run_id'],{'schema_version':'1.3.0','partial_marker':'preserved'})
    assert service.run_once()
    result=service.read(actor,UUID(run['run_id']))
    assert result['state']=='failed' and result['report']['partial_marker']=='preserved'


def test_http_new_analyse_rejects_invalid_filters_and_queues_three_refs(radar,monkeypatch):
    from fastapi import FastAPI
    from bervel.modules.tender_radar.api import radar_router
    from http_client import ASGITestClient
    service,actor,run,refs=full_setup(radar,monkeypatch)
    app=FastAPI();app.include_router(radar_router(service,lambda:actor))
    body={name:{'artifact_id':str(ref.artifact_id)} for name,ref in zip(('procurement_source','stock_source','economics_source'),refs)}
    body.update(command_key='http',filters={'min_margin_percent':'15'})
    with ASGITestClient(app) as http:
        result=http.post('/api/v0.1/radar/analyse',json=body)
        assert result.status_code==202,result.text
        assert result.json()['selection']['economics_source']['artifact_id']==str(refs[2].artifact_id)
        assert 'revision' not in result.json()['selection']['economics_source']
        assert http.post('/api/v0.1/radar/analyse',json=body|{
            'procurement_source':body['procurement_source']|{'revision':1}}).status_code==422
        for filters in ({'min_days_remaining':True},{'min_margin_percent':'NaN'},{'focus':'a'*1001}):
            assert http.post('/api/v0.1/radar/analyse',json=body|{'filters':filters}).status_code==422

def test_economics_adapter_preserves_unknowns_and_rejects_mixed_kind():
    from bervel.modules.tender_radar.infrastructure.economics import EconomicsImporter
    head='sku,unit,currency,sale_price,unit_cost,fixed_cost,logistics_cost,packaging_per_unit,capacity_per_day,production_lead_days,delivery_lead_days,data_kind\n'
    row='P1,шт,RUB,10,,0,0,0,100,0,1,mock\n'
    parsed=EconomicsImporter().parse((head+row).encode(),'e.csv')
    assert parsed['items'][0]['unit_cost'] is None and parsed['items'][0]['row']==2
    with pytest.raises(Exception):EconomicsImporter().parse((head+row+row.replace('P1','P2').replace('mock','reported')).encode(),'e.csv')
    with pytest.raises(Exception):EconomicsImporter().parse((head+row.replace('10,','NaN,')).encode(),'e.csv')


def test_completed_portions_survive_a_later_failure(radar,monkeypatch):
    from test_radar import add
    from uuid import UUID
    import json
    service,actor,original,refs=full_setup(radar,monkeypatch)
    # Finish the setup job, then import21 independent tenders (two transport portions).
    service.run_once()
    content=normalized(title='Болт ГОСТ7798 M16x50 8.8 цинк: 10 шт. Поставка в течение 10 дней',price='1000',number='0')
    for i in range(1,21):content+=normalized(title='Болт ГОСТ7798 M16x50 8.8 цинк: 10 шт. Поставка в течение 10 дней',price='1000',number=str(i)).splitlines(keepends=True)[1]
    purchase=add(service.artifacts,actor,'p.csv',content)
    service.artifacts.set_usage_role(actor,purchase.artifact_id,'procurement')
    good=service.model
    class Flaky:
        calls=0
        def generate(self,*args,**kwargs):
            self.calls+=1
            if self.calls==2:raise RuntimeError('bad portion')
            return good.generate(*args,**kwargs)
    service.model=Flaky()
    run=service.analyse(actor,purchase,*refs[1:],{},'many')
    service.run_once();result=service.read(actor,UUID(run['run_id']))
    assert result['state']=='failed'
    report=result['report']
    assert report['counts']['total']==21 and report['coverage']['accounted_rows']==21
    assert 0<report['counts']['take_in_work']<21
    assert report['counts']['take_in_work']+report['counts']['not_analysed']==21
    assert report['counts']['model_analysed']==report['counts']['take_in_work']


def test_oversized_row_does_not_discard_another_valid_tender(radar,monkeypatch):
    from test_radar import add
    from uuid import UUID
    service,actor,_,refs=full_setup(radar,monkeypatch);service.run_once()
    title='Болт ГОСТ7798 M16x50 8.8 цинк: 10 шт. Поставка в течение 10 дней'
    content=normalized(title=title,price='1000',number='normal',description='')
    # Many mandatory lines exhaust response budget while original fields stay valid.
    raw=normalized(title=title,price='1000',number='large',description='Болт: 10 шт\n'*500)
    import csv,io
    rows=list(csv.DictReader(io.StringIO(content.decode())))+list(csv.DictReader(io.StringIO(raw.decode())))
    output=io.StringIO();writer=csv.DictWriter(output,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
    purchase=add(service.artifacts,actor,'p.csv',output.getvalue().encode())
    service.artifacts.set_usage_role(actor,purchase.artifact_id,'procurement')
    run=service.analyse(actor,purchase,*refs[1:],{},'large')
    service.run_once();report=service.read(actor,UUID(run['run_id']))['report']
    assert report['counts']['take_in_work']==1
    assert report['counts']['not_analysed']==1
    assert report['portion_errors'][0]['code']=='RADAR_CONTEXT_LIMIT'

def test_omitted_mandatory_quantity_line_blocks_false_match(radar,monkeypatch):
    from uuid import UUID
    title='Болт ГОСТ7798 M16x50 8.8 цинк: 10 шт.\nГайка ГОСТ5915 M16: 20 шт. Поставка в течение 10 дней'
    service,actor,run,_=full_setup(radar,monkeypatch,description=title)
    # Provider reports only the first mandatory line and incorrectly claims completeness.
    service.run_once();item=service.read(actor,UUID(run['run_id']))['report']['items'][0]
    assert item['decision']=='needs_review'
    assert not item['participation_ready']

def test_foreign_currency_filter_does_not_compare_to_ruble_amount():
    from bervel.modules.tender_radar.full import filter_rows,validate_filters
    accepted,excluded=filter_rows([tender(currency='KZT',price='1')],[SOURCE],NOW,validate_filters({'min_price':'100'}))
    assert not accepted
    assert 'валюта' in excluded['one']['reason'].casefold()


def test_excel_content_without_identifier_is_not_silently_skipped():
    import io,openpyxl
    from test_tender_importer import xlsx
    book=openpyxl.load_workbook(io.BytesIO(xlsx()))
    for cell in list(book.active[5]):book.active.cell(7,cell.column,cell.value)
    book.active['A7']='0034';book.active['I7']='https://example.org/0034'
    book.active['A5']=None
    output=io.BytesIO();book.save(output);book.close()
    with pytest.raises(Exception):TenderImporter().parse(output.getvalue(),'t.xlsx')

def test_unreadable_large_demand_number_requires_review(radar,monkeypatch):
    from uuid import UUID
    title='Болт ГОСТ7798 M16x50 8.8 цинк: 10 шт. Гайка: 999999999999999999999 шт. Поставка в течение 10 дней'
    service,actor,run,_=full_setup(radar,monkeypatch,description=title)
    service.run_once();result=service.read(actor,UUID(run['run_id']))
    assert result['state']=='completed',result['error']
    assert result['report']['items'][0]['decision']=='needs_review'

@pytest.mark.parametrize('kind',['reported','mock'])
@pytest.mark.parametrize('has_risk',[True,False])
def test_full_report_preserves_unresolved_model_conditions_without_blocking_clear_cases(kind,has_risk):
    from bervel.modules.tender_radar.full import full_report,validate_filters
    source=SOURCE|{'excerpt':'Болт ГОСТ7798 M16x50 8.8 цинк'}
    row=tender(title='Болт ГОСТ7798 M16x50 8.8 цинк: 10 шт',price='1000',
        description='Поставка в течение 10 дней.'+(' Обязателен сертификат лаборатории.' if has_risk else ''))
    unresolved='Наличие обязательного сертификата лаборатории не подтверждено.'
    next_step='Проверить сертификат лаборатории.'
    value=assessment(tender_quote='Болт',profile_quote='Болт',
        positions=[position(quantity='10',quote=row['title'])],positions_complete=True,
        risks=[unresolved] if has_risk else [],next_steps=[next_step] if has_risk else [])
    stock={'kind':kind,'items':[dict(sku='P1',product='Болт',free_quantity='10',unit='шт',
        specs=dict(standard='ГОСТ7798',thread='M16',length_mm='50',strength='8.8',coating='цинк'),row=2,as_of=None)]}
    economics={'kind':kind,'items':[dict(sku='P1',unit='шт',currency='RUB',sale_price='10',unit_cost='8',
        fixed_cost='0',logistics_cost='0',packaging_per_unit='0',capacity_per_day='100',production_lead_days='0',delivery_lead_days='1',row=2)]}
    report=full_report([row],[source],[value],NOW,prefiltered={},stock=stock,economics=economics,
        filters=validate_filters({}),errors=[],import_data={'duplicate_rows':0,'skipped_rows':0},complete=True)
    item=report['items'][0]
    assert item['stock_check']['status']=='pass' and item['economics_check']['status']=='pass'
    if has_risk:
        assert item['decision']=='needs_review'
        assert not item['participation_ready'] and item['technical_readiness']=='unknown'
        assert unresolved in item['risks'] and next_step in item['next_steps']
    else:
        assert item['decision']=='take_in_work'
        assert item['participation_ready']==(kind=='reported')
        assert item['technical_readiness']=='ready'
        assert not item['risks'] if kind=='reported' else all('МОК' in r for r in item['risks'])
