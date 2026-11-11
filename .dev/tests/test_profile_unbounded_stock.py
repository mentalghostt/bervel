"""Warehouse/profile volume does not change whether valid facts can be saved."""
import json
from uuid import uuid4

from fastapi.testclient import TestClient

from bervel.core.api.app import create_platform_app
from bervel.core.config import Settings
from bervel.core.models import ActorContext,RegisterArtifact
from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
from bervel.modules.ai_tender_radar.service import AiTenderRadar
from bervel.modules.company_settings.api import company_settings_router
from bervel.modules.company_settings.infrastructure.repository import PostgresCompanySettingsRepository
from bervel.modules.company_settings.models import validate_profile
from bervel.modules.company_settings.service import CompanySettings


def large_csv():
    return ('sku,product,free_quantity,unit,note\n'+''.join(
        f'S{i},Bolt {i},0,pc,'+'x'*600+'\n' for i in range(10001))).encode()


def test_large_profile_has_no_byte_or_catalog_row_ceiling():
    data=dict(stock_artifact_id=str(uuid4()),stock_sha256='a'*64,products=[
        dict(id=f'p{i}',sku=f'S{i}',name=f'Bolt {i}',unit='pc',enabled=True,manufactured_before=None,
             attributes={'note':'x'*1200}) for i in range(6001)],experimental_products=[],include_experimental=False,
        production=[],resources=[],scoring_policy={})
    assert len(json.dumps(data).encode())>5*1024*1024
    result=validate_profile(data)
    assert len(result['products'])==6001
    assert result['products'][-1]['sku']=='S6000'


def test_warehouse_larger_than_five_mib_is_read_to_the_last_row():
    content=large_csv()
    assert len(content)>5*1024*1024
    parsed=read_stock(content)
    assert len(parsed['rows'])==10001
    assert parsed['rows'][-1]['sku']=='S10000'
    AiTenderRadar._check_stock({'filename':'stock.csv','size':len(content),'usage_role':'general'})


def test_large_unlabelled_csv_preview_save_and_idempotent_replay(services,pg_engine,tmp_path):
    artifacts,_,_,original=services
    actor=ActorContext(original.actor_id,original.data_scope,original.permissions|{'settings:read','settings:write'})
    ref=artifacts.register(actor,RegisterArtifact(large_csv(),'warehouse.csv','text/csv',{},'large-unlabelled-stock'))
    assert artifacts.metadata(actor,ref)['usage_role']=='general'
    settings=Settings(pg_engine.url.render_as_string(hide_password=False),tmp_path/'objects',{'a'*32:actor})
    app,dependency=create_platform_app(settings,pg_engine,artifacts)
    company=CompanySettings(PostgresCompanySettingsRepository(pg_engine),artifacts,read_stock)
    app.include_router(company_settings_router(company,dependency))
    with TestClient(app,headers={'Authorization':'Bearer '+'a'*32}) as client:
        preview=client.post('/api/v0.1/settings/company/preview',json={'stock_artifact_id':str(ref.artifact_id)})
        assert preview.status_code==200,preview.text[:500]
        data=preview.json()['profile']
        assert len(data['products'])==10001
        body={'command_key':'save-large-profile','base_snapshot_id':None,'profile':data}
        assert len(json.dumps(body).encode())>5*1024*1024
        saved=client.put('/api/v0.1/settings/company',json=body)
        assert saved.status_code==200,saved.text[:500]
        first=saved.json()['snapshot']
        repeated=client.put('/api/v0.1/settings/company',json=body)
        assert repeated.status_code==200,repeated.text[:500]
        assert repeated.json()['snapshot']['id']==first['id']
        result=client.get('/api/v0.1/settings/company').json()['snapshot']
        assert result['id']==first['id']
        assert len(result['profile']['products'])==10001
        assert result['profile']['products'][-1]['sku']=='S10000'
        assert artifacts.metadata(actor,ref)['usage_role']=='general'
