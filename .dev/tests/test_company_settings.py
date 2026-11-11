from copy import deepcopy
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, RegisterArtifact


def product(**changes):
    return dict(id='p1', sku='S1', name='Болт', unit='шт', attributes={'family':'Крепёж'},
                enabled=True, manufactured_before=None, **changes)


def profile():
    return dict(stock_artifact_id=str(uuid4()), stock_sha256='a'*64, products=[product()],
                experimental_products=[], include_experimental=False, production=[], resources=[], scoring_policy={})


def test_validation_and_active_catalog_do_not_invent_production():
    from bervel.modules.company_settings.models import validate_profile, active_products, stock_for_profile, search_phrases
    data = profile()
    data['experimental_products'] = [dict(id='e1', name='Шайба', unit='шт', attributes={}, enabled=True, product_id=None)]
    checked = validate_profile(data)
    assert [p['id'] for p in active_products(checked)] == ['p1']
    assert search_phrases(checked) == ['Крепёж']
    checked['include_experimental'] = True
    stock = stock_for_profile({'data_kind':'reported', 'rows':[]}, checked)
    assert [(r['sku'], r['free_quantity']) for r in stock['rows']] == [('S1','0'),('EXP:e1','0')]
    assert checked['products'][0]['manufactured_before'] is None


def test_legacy_manufacturing_fields_are_preserved_without_becoming_new_requirements():
    from bervel.modules.company_settings.models import validate_profile
    data = profile()
    data['products'][0].update(manufactured_before=True, evidence='warehouse:source:row:2')
    assert validate_profile(data) == data


@pytest.mark.parametrize('policy', [{'target_margin':'0'}, {'comfortable_utilization':'1'}, {'stress_rate_factor':'0'}])
def test_policy_cannot_create_division_by_zero_or_unusable_scale(policy):
    from bervel.modules.company_settings.models import validate_profile
    data = profile()
    data['scoring_policy'] = policy
    with pytest.raises(CoreError):
        validate_profile(data)


@pytest.mark.parametrize('mutation', [
    lambda p: p['products'][0].update(manufactured_before='true'),
    lambda p: p['products'].append(deepcopy(p['products'][0])),
    lambda p: p['products'][0].update(enabled='false'),
    lambda p: p['experimental_products'].append(dict(id='x', name='X', unit='шт', attributes={}, enabled=True, product_id='missing')),
    lambda p: p.update(stock_sha256='wrong'),
    lambda p: p['resources'].append(dict(group_id='g', calendar=[7], free_capacity_by_day={}, confirmed_at=None)),
    lambda p: p['resources'].append(dict(group_id='g', calendar=[0], free_capacity_by_day={'2026-11-05':'NaN'}, confirmed_at=None)),
    lambda p: p['production'].append(dict(product_id='p1', resource_group_id='g', units_per_workday='100', production_unit_cost=None,
                                        currency=None, setup_workdays='0', material_ready_at=None, confirmed_at='2026-11-05T10:00:00+03:00')),
    lambda p: p.update(scoring_policy={'target_margin':'NaN'}),
])
def test_rejects_invalid_facts_references_and_numbers(mutation):
    from bervel.modules.company_settings.models import validate_profile
    data = profile()
    mutation(data)
    with pytest.raises(CoreError) as caught:
        validate_profile(data)
    assert caught.value.code == 'VALIDATION_ERROR'


def test_legacy_linked_experimental_keeps_stock_once_and_disabled_stock_is_removed():
    from bervel.modules.company_settings.models import stock_for_profile, search_phrases
    data = profile()
    data['products'][0]['enabled'] = False
    data['include_experimental'] = True
    data['experimental_products'] = [dict(id='e', product_id='p1', name='Болт новый', unit='шт', attributes={}, enabled=True)]
    row = dict(row=2, sku='S1', product='Болт', free_quantity='12', unit='шт', unit_cost=None, currency=None, as_of=None, attributes={})
    other = dict(row, sku='S2', product='Выключено')
    result = stock_for_profile({'data_kind':'reported','rows':[row,other]}, data, legacy_links=True)
    assert [(r['sku'],r['free_quantity']) for r in result['rows']] == [('S1','12')]
    data['include_experimental'] = False
    assert stock_for_profile({'data_kind':'reported','rows':[row,other]}, data)['rows'] == []
    assert search_phrases(data) == []


def test_search_splits_long_names_without_silent_truncation():
    from bervel.modules.company_settings.models import search_phrases
    data = profile()
    data['products'][0].update(name='a'*950, attributes={})
    phrases = search_phrases(data)
    assert all(0 < len(p) <= 500 for p in phrases)
    assert ''.join(phrases) == 'a'*950


def test_postgres_immutable_idempotent_scoped_snapshots(pg_engine):
    from bervel.modules.company_settings.infrastructure.repository import PostgresCompanySettingsRepository
    repo = PostgresCompanySettingsRepository(pg_engine)
    actor = ActorContext('u','scope',frozenset())
    first = repo.save(actor, None, profile(), 'one')
    assert repo.save(actor, None, first['profile'], 'one') == first
    assert repo.read('other') is None
    assert repo.read_snapshot('other', first['id']) is None
    second_data = deepcopy(first['profile'])
    second_data['products'][0]['name'] = 'Обновлённый'
    second_data['experimental_products'] = [dict(id='e',product_id=None,name='Новый',unit='шт',attributes={},enabled=True)]
    second = repo.save(actor, first['id'], second_data, 'two')
    assert repo.read('scope') == second
    assert repo.read_snapshot('scope', first['id']) == first
    assert second['profile']['experimental_products'][0]['name'] == 'Новый'
    with pytest.raises(CoreError) as stale:
        repo.save(actor, first['id'], second_data, 'three')
    assert stale.value.code == 'SETTINGS_CONFLICT'
    with pytest.raises(CoreError) as reused:
        repo.save(actor, None, second_data, 'one')
    assert reused.value.code == 'IDEMPOTENCY_CONFLICT'


def test_preview_preserves_manual_overrides_and_zero_stock(services, pg_engine):
    from bervel.modules.company_settings.service import CompanySettings
    from bervel.modules.company_settings.infrastructure.repository import PostgresCompanySettingsRepository
    from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
    artifacts, _, _, original = services
    actor = ActorContext(original.actor_id,original.data_scope,original.permissions | {'settings:read','settings:write'})
    csv = b'sku,product,free_quantity,unit\nS1,Bolt,0,pc\n'
    ref = artifacts.register(actor, RegisterArtifact(csv,'stock.csv','text/csv',{},'upload-settings'))
    artifacts.set_usage_role(actor,ref.artifact_id,'stock')
    service = CompanySettings(PostgresCompanySettingsRepository(pg_engine),artifacts,read_stock)
    preview = service.preview(actor,ref.artifact_id)
    assert 'manufactured_before' not in preview['profile']['products'][0]
    assert len(preview['profile']['products']) == 1
    preview['profile']['products'][0].update(name='Manual', enabled=False)
    saved = service.save(actor,None,preview['profile'],'save-settings')
    new = service.preview(actor,ref.artifact_id,saved['id'])
    assert new['profile']['products'][0]['name'] == 'Manual'
    assert new['profile']['products'][0]['enabled'] is False
    assert new['warnings']
    assert service.read_snapshot(actor.data_scope,saved['id']) == saved
    with pytest.raises(CoreError):
        service.read(ActorContext('u','scope',frozenset()))

def test_http_contract_rejects_unknown_fields_and_exposes_snapshots(services, pg_engine):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bervel.modules.company_settings.api import company_settings_router
    from bervel.modules.company_settings.service import CompanySettings
    from bervel.modules.company_settings.infrastructure.repository import PostgresCompanySettingsRepository
    from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
    artifacts, _, _, original = services
    actor = ActorContext(original.actor_id,original.data_scope,original.permissions | {'settings:read','settings:write'})
    ref = artifacts.register(actor,RegisterArtifact(b'sku,product,free_quantity,unit\nS1,Bolt,0,pc\n','stock.csv','text/csv',{},'api-settings'))
    artifacts.set_usage_role(actor,ref.artifact_id,'stock')
    app = FastAPI()
    service = CompanySettings(PostgresCompanySettingsRepository(pg_engine),artifacts,read_stock)
    app.include_router(company_settings_router(service,lambda: actor))
    client = TestClient(app)
    assert client.get('/api/v0.1/settings/company').json() == {'snapshot':None}
    bad = client.post('/api/v0.1/settings/company/preview',json={'stock_artifact_id':str(ref.artifact_id),'extra':True})
    assert bad.status_code == 422
    preview = client.post('/api/v0.1/settings/company/preview',json={'stock_artifact_id':str(ref.artifact_id)})
    assert preview.status_code == 200
    data = preview.json()['profile']
    data['products'][0]['enabled'] = 'false'
    assert client.put('/api/v0.1/settings/company',json={'command_key':'api-save','base_snapshot_id':None,'profile':data}).status_code == 422
    data['products'][0]['enabled'] = True
    data['resources'] = [{'group_id': 'g', 'calendar': [0], 'enabled': False, 'default_daily_capacity': '0.8'}]
    data['experimental_products'] = [{'id': 'exp', 'sku': 'S1', 'name': 'Experiment', 'unit': 'pc',
                                     'enabled': True, 'attributes': {'material': 'steel'}}]
    data['production'] = [{'product_id': 'exp', 'resource_group_id': 'g', 'units_per_workday': '10',
                           'production_unit_cost': '5', 'currency': 'RUB', 'setup_workdays': '0',
                           'material_ready_at': None, 'evidence': 'Factory data'}]
    data['scoring_policy'] = {'high_score': 90, 'stress_price_factor': '0.9', 'max_age_by_source': {'stock': '0'}}
    saved = client.put('/api/v0.1/settings/company',json={'command_key':'api-save','base_snapshot_id':None,'profile':data})
    assert saved.status_code == 200, saved.text
    snapshot = saved.json()['snapshot']
    assert snapshot['profile'] == data
    assert snapshot['effective_scoring_policy'] == {
        'version': 'score-v2', 'target_margin': '0.30', 'comfortable_utilization': '0.70',
        'minimum_production_factor': '0.60', 'high_score': 90, 'medium_score': 50}
    assert client.get('/api/v0.1/settings/company').json() == saved.json()


def test_setup_cost_requires_explicit_currency():
    from bervel.modules.company_settings.models import validate_profile
    data = profile()
    data['products'][0].update(manufactured_before=True,evidence='user-confirmed')
    data['resources'] = [dict(group_id='g',calendar=[0,1,2,3,4],free_capacity_by_day={},confirmed_at=None)]
    capability = dict(product_id='p1',resource_group_id='g',units_per_workday=None,production_unit_cost=None,
                      currency=None,setup_workdays='1',material_ready_at=None,confirmed_at=None,setup_cost='100')
    data['production'] = [capability]
    with pytest.raises(CoreError):
        validate_profile(data)
    capability.update(currency='RUB',confirmed_at='2026-11-05T10:00:00+03:00',evidence='factory:confirmed')
    assert validate_profile(data)['production'][0]['setup_cost'] == '100'


@pytest.mark.parametrize('section,key', [('experimental_products','product_id'),('production','product_id'),('production','resource_group_id')])
def test_malformed_references_raise_validation_not_typeerror(section,key):
    from bervel.modules.company_settings.models import validate_profile
    data = profile()
    data['experimental_products'] = [dict(id='e',product_id=None,name='X',unit='шт',attributes={},enabled=True)]
    data['production'] = [dict(product_id='p1',resource_group_id='g',units_per_workday=None,production_unit_cost=None,
                               currency=None,setup_workdays='0',material_ready_at=None,confirmed_at=None)]
    data[section][0][key] = []
    with pytest.raises(CoreError) as error:
        validate_profile(data)
    assert error.value.code == 'VALIDATION_ERROR'


def test_manual_catalog_fields_drive_matching_without_changing_stock_quantity():
    from bervel.modules.company_settings.models import stock_for_profile
    data = profile()
    data['products'][0].update(name='Болт подтверждённый',attributes={'size':'M12'})
    row = dict(row=2,sku='S1',product='Old',unit='шт',free_quantity='2',unit_cost='1',currency='RUB',as_of=None,attributes={'size':'M10'})
    result = stock_for_profile({'data_kind':'reported','rows':[row]},data)
    assert result['rows'][0]['product'] == 'Болт подтверждённый'
    assert result['rows'][0]['attributes'] == {'size':'M12'}
    assert result['rows'][0]['free_quantity'] == '2'
    assert row['product'] == 'Old'


def test_concurrent_initial_saves_cannot_overwrite_each_other(pg_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from bervel.modules.company_settings.infrastructure.repository import PostgresCompanySettingsRepository
    repo = PostgresCompanySettingsRepository(pg_engine)
    barrier = Barrier(2)
    def save(index):
        barrier.wait()
        try:
            return repo.save(ActorContext('u','concurrent',frozenset()),None,profile(),f'c{index}')
        except CoreError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save,[1,2]))
    assert len([r for r in results if isinstance(r,dict)]) == 1
    assert [r for r in results if isinstance(r,str)] == ['SETTINGS_CONFLICT']


def test_permissions_scope_and_stock_digest_are_enforced(services,pg_engine):
    from bervel.modules.company_settings.service import CompanySettings
    from bervel.modules.company_settings.infrastructure.repository import PostgresCompanySettingsRepository
    from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
    artifacts, _, _, original = services
    actor = ActorContext(original.actor_id,original.data_scope,original.permissions | {'settings:read','settings:write'})
    ref = artifacts.register(actor,RegisterArtifact(b'sku,product,free_quantity,unit\nS1,Bolt,0,pc\n','stock.csv','text/csv',{},'permissions-stock'))
    artifacts.set_usage_role(actor,ref.artifact_id,'stock')
    service = CompanySettings(PostgresCompanySettingsRepository(pg_engine),artifacts,read_stock)
    data = service.preview(actor,ref.artifact_id)['profile']
    viewer = ActorContext(actor.actor_id,actor.data_scope,frozenset({'settings:read','artifact:read'}))
    with pytest.raises(CoreError) as forbidden:
        service.save(viewer,None,data,'forbidden')
    assert forbidden.value.code == 'FORBIDDEN'
    broken = deepcopy(data)
    broken['stock_sha256'] = '0'*64
    with pytest.raises(CoreError) as wrong_hash:
        service.save(actor,None,broken,'wrong-hash')
    assert wrong_hash.value.code == 'VALIDATION_ERROR'
    first = service.save(actor,None,data,'correct')
    with pytest.raises(CoreError) as missing:
        service.preview(ActorContext(actor.actor_id,'other',actor.permissions),ref.artifact_id,first['id'])
    assert missing.value.code in {'NOT_FOUND','FORBIDDEN'}
    assert service.read(ActorContext('other','other',frozenset({'settings:read'}))) is None
