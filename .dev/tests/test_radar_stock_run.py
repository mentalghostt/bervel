"""Persist selected stock/filters and execute the complete controlled pipeline."""
from decimal import Decimal
import json
from uuid import UUID
import pytest

from test_radar import radar,add
from test_tender_importer import normalized
from test_radar_analysis import assessment
from test_radar_stock import position
from bervel.core.errors import CoreError


class PositionModel:
    def __init__(self):self.calls=[]
    def generate(self,key,prompt,**kwargs):
        data=json.loads(prompt.split('\n',1)[1]);self.calls.append(data)
        values=[assessment(r['id'],tender_quote='Болт М16',profile_quote='Болт',
            positions=[position(quote='Болт М16: 1000 шт')],positions_complete=True) for r in data['TENDERS']]
        return {'model':'position-fixture','answer':json.dumps({'items':values})}


def setup_run(radar,**options):
    service,artifacts,actor,repo,_=radar
    from bervel.modules.tender_radar.infrastructure.stock import StockImporter
    service.stock_importer=StockImporter();service.model=PositionModel()
    batch=service.import_batch(actor,add(artifacts,actor,'t.csv',normalized(title='Болт М16: 1000 шт')),'batch')
    profile=add(artifacts,actor,'profile.txt','Болт М16'.encode())
    stock=add(artifacts,actor,'stock.csv','sku,product,free_quantity,unit,data_kind\nP1,Болт,900,шт,mock\n'.encode())
    run=service.start(actor,UUID(batch['batch_id']),[profile],'','run',stock_ref=stock,max_shortage_percent=options.get('percent','10'))
    return service,actor,batch,profile,stock,run


def test_pinned_stock_percent_full_pipeline_and_command_fingerprint(radar):
    service,actor,batch,profile,stock,run=setup_run(radar)
    assert run['selection']['max_shortage_percent']=='10'
    assert run['selection']['stock_source']['artifact_id']==str(stock.artifact_id)
    assert service.start(actor,UUID(batch['batch_id']),[profile],'','run',stock_ref=stock,max_shortage_percent='10.00')==run
    with pytest.raises(CoreError):service.start(actor,UUID(batch['batch_id']),[profile],'','run',stock_ref=stock,max_shortage_percent='9.99')
    service.run_once();completed=service.read(actor,UUID(run['run_id']))
    assert completed['state']=='completed',completed['error']
    item=completed['report']['items'][0]
    assert item['decision']=='needs_review' and item['demo'] is True
    assert item['stock_check']['checks'][0]['shortage_percent']=='10'
    assert completed['report']['stock_source']['artifact_id']==str(stock.artifact_id)
    assert 'revision' not in completed['report']['stock_source']
    assert completed['report']['funnel']['model_requests']==1


@pytest.mark.parametrize('value',['-1','100.01','1.001','NaN',True])
def test_bad_tolerance_never_enqueues_a_job(radar,value):
    with pytest.raises(CoreError):setup_run(radar,percent=value)


def test_zero_tolerance_rejects_shortage(radar):
    service,actor,_,_,_,run=setup_run(radar,percent='0')
    service.run_once();report=service.read(actor,UUID(run['run_id']))['report']
    assert report['items'][0]['decision']=='excluded'
    assert report['items'][0]['decision_stage']=='quantity'


def test_http_validates_new_filters_and_returns_typed_quantity(radar,pg_engine,tmp_path):
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from http_client import ASGITestClient
    from bervel.modules.tender_radar.api import ReportResponse
    service,actor,_,_,_,run=setup_run(radar)
    service.run_once()
    settings=Settings(pg_engine.url.render_as_string(hide_password=False),tmp_path/'objects',{'a'*32:actor})
    with ASGITestClient(create_app(settings)) as client:
        response=client.get('/api/v0.1/radar/runs/'+run['run_id'],headers={'Authorization':'Bearer '+'a'*32})
        assert response.status_code==200
        assert response.json()['report']['items'][0]['stock_check']['checks'][0]['required']=='1000'
        assert response.json()['selection']['max_shortage_percent']=='10'
    # Historical reports remain readable without any new quantity fields.
    legacy=service.read(actor,UUID(run['run_id']))['report']|{'schema_version':'1.1.0'}
    for key in ('stock_kind','stock_source','funnel','selection'):legacy.pop(key,None)
    assert ReportResponse.model_validate(legacy).schema_version=='1.1.0'
