from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from bervel.core.models import ActorContext,RegisterArtifact
from bervel.core.errors import CoreError
from bervel.modules.company_settings.models import validate_profile,stock_for_profile,search_phrases
from bervel.modules.company_settings.service import CompanySettings
from bervel.modules.company_settings.infrastructure.repository import PostgresCompanySettingsRepository
from bervel.modules.company_settings.api import company_settings_router
from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
from bervel.modules.ai_tender_radar.production import evaluate_fulfillment
from test_company_settings import profile
from test_ai_radar_production import case,NOW


def current_profile():
    data=profile()
    data['products'][0].pop('manufactured_before')
    data['experimental_products']=[dict(id='exp',name='Experimental',unit='шт',attributes={},enabled=True)]
    return data


def test_official_catalog_and_independent_experimental_need_no_removed_fields():
    checked=validate_profile(current_profile())
    assert 'manufactured_before' not in checked['products'][0]
    assert 'product_id' not in checked['experimental_products'][0]
    assert search_phrases(checked)==['Крепёж']
    checked['include_experimental']=True
    assert search_phrases(checked)==['Крепёж','Experimental']


def test_capacity_facts_need_evidence_without_a_confirmation_date_or_manufacturing_flag():
    data=current_profile()
    data['resources']=[dict(group_id='g',calendar=[0,1,2,3,4],free_capacity_by_day={},confirmed_at=NOW.isoformat())]
    data['production']=[dict(product_id='p1',resource_group_id='g',units_per_workday='20',production_unit_cost='5',
        currency='RUB',setup_workdays='0',material_ready_at=NOW.isoformat(),confirmed_at=NOW.isoformat(),evidence='factory:capacity')]
    assert validate_profile(data)['production'][0]['units_per_workday']=='20'
    data['production'][0]['confirmed_at']=None
    assert validate_profile(data)['production'][0]['units_per_workday']=='20'
    data['production'][0].pop('evidence')
    with pytest.raises(CoreError):validate_profile(data)


@pytest.mark.parametrize('old_flag',['absent',None,False])
def test_current_manufacturing_uses_official_membership_instead_of_legacy_flag(old_flag):
    data=case()
    data[3]['products'][0].pop('evidence',None)
    if old_flag=='absent':data[3]['products'][0].pop('manufactured_before')
    else:data[3]['products'][0]['manufactured_before']=old_flag
    plan=evaluate_fulfillment(*data,now=NOW)
    assert plan['version']==4
    assert plan['status']=='feasible'
    assert plan['production_allocations'][0]['quantity']=='60'


def test_official_membership_does_not_invent_a_missing_rate_or_material_date():
    for field in ('units_per_workday','material_ready_at','production_unit_cost'):
        data=case();data[3]['products'][0].pop('manufactured_before');data[3]['products'][0].pop('evidence')
        data[3]['production'][0][field]=None
        assert evaluate_fulfillment(*data,now=NOW)['status']=='needs_review'


def test_historical_production_rules_are_still_explicitly_reproducible():
    data=case();data[3]['products'][0]['manufactured_before']=False
    plan=evaluate_fulfillment(*data,now=NOW,rule_version=1)
    assert plan['version']==1
    assert plan['status']=='infeasible'
    data[3]['products'][0]['manufactured_before']=None
    assert evaluate_fulfillment(*data,now=NOW,rule_version=1)['status']=='needs_review'


def test_audit_production_invariant_uses_the_saved_rule_version():
    from bervel.modules.ai_tender_radar.audit import check_invariants
    tender,stock,analysis,settings=case()
    settings['products'][0].pop('manufactured_before')
    settings['products'][0].pop('evidence')
    plan=evaluate_fulfillment(tender,stock,analysis,settings,now=NOW)
    card=dict(analysis_id='catalog',analysis=analysis,fulfillment=plan)
    assert not any(f['reason']=='UNCONFIRMED_PRODUCTION' for f in check_invariants(card,stock,settings))
    plan['version']=1
    assert any(f['reason']=='UNCONFIRMED_PRODUCTION' for f in check_invariants(card,stock,settings))


def test_legacy_experimental_link_cannot_enable_a_disabled_official_product():
    data=profile();data['products'][0]['enabled']=False
    data['include_experimental']=True
    data['experimental_products']=[dict(id='exp',name='Experimental',unit='шт',attributes={},enabled=True,product_id='p1')]
    physical=dict(sku='S1',product='Bolt',unit='шт',free_quantity='10',attributes={})
    stock=dict(data_kind='reported',rows=[physical])
    current=stock_for_profile(stock,data)
    assert [r['sku'] for r in current['rows']]==['EXP:exp']
    assert current['rows'][0]['free_quantity']=='0'
    assert [r['sku'] for r in stock_for_profile(stock,data,legacy_links=True)['rows']]==['S1']
    data['include_experimental']=False
    assert stock_for_profile(stock,data)['rows']==[]


def test_experimental_link_is_not_a_current_manufacturing_route():
    data=case();data[3]['products'][0]['enabled']=False
    data[3]['include_experimental']=True
    data[3]['experimental_products']=[dict(id='exp',name='Experimental',unit='шт',attributes={},enabled=True,product_id='product')]
    assert evaluate_fulfillment(*data,now=NOW)['production_allocations']==[]
    assert evaluate_fulfillment(*data,now=NOW,rule_version=1)['status']=='feasible'


def test_import_new_save_and_legacy_exact_replay_keep_immutable_snapshots(services,pg_engine):
    artifacts,_,_,owner=services
    actor=ActorContext(owner.actor_id,owner.data_scope,owner.permissions|{'settings:read','settings:write'})
    ref=artifacts.register(actor,RegisterArtifact(b'sku,product,free_quantity,unit\nS1,Bolt,0,pc\n','warehouse.csv','text/csv',{},'catalog-v2-stock'))
    repo=PostgresCompanySettingsRepository(pg_engine)
    company=CompanySettings(repo,artifacts,read_stock)
    preview=company.preview(actor,ref.artifact_id)['profile']
    assert preview['include_experimental'] is False
    assert 'manufactured_before' not in preview['products'][0]
    assert 'evidence' not in preview['products'][0]
    legacy=deepcopy(preview)
    legacy['products'][0].update(manufactured_before=None,evidence='warehouse:old-import')
    legacy['experimental_products']=[dict(id='exp',name='Exp',unit='pc',attributes={},enabled=True,product_id=None)]
    first=repo.save(actor,None,legacy,'legacy-command')
    app=FastAPI();app.include_router(company_settings_router(company,lambda:actor))
    with TestClient(app) as client:
        replay=client.put('/api/v0.1/settings/company',json={'command_key':'legacy-command','base_snapshot_id':None,'profile':legacy})
        assert replay.status_code==200,replay.text[:500]
        assert replay.json()['snapshot']['id']==str(first['id'])
        saved=client.put('/api/v0.1/settings/company',json={'command_key':'v2-command','base_snapshot_id':str(first['id']),'profile':preview})
        assert saved.status_code==200,saved.text[:500]
        assert 'manufactured_before' not in saved.json()['snapshot']['profile']['products'][0]
    assert repo.read_snapshot(actor.data_scope,first['id'])['profile']==legacy
