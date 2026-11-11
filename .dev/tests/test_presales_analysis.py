from copy import deepcopy
import json

def fixture(quantity='80', free='100'):
    line = dict(line_id='L',name='Болт М12',quantity=quantity,unit='шт',requirements=[],evidence=[],client_unit_price=None,currency=None)
    request = dict(title='Заказ',lines=[line],lists=[],complete=True,client_terms={},settings=dict(target_margin_percent='50',special_min_margin_percent='70'))
    row = dict(sku='B',product='Болт М12',unit='шт',free_quantity=free,attributes={},unit_cost='100',currency='RUB')
    product = dict(id='P',sku='B',name='Болт М12',unit='шт',attributes={},enabled=True,manufactured_before=True,evidence='Каталог')
    profile = dict(products=[product],experimental_products=[],include_experimental=False,production=[],resources=[],scoring_policy={'target_margin':'.9'})
    return request,dict(id='S',stock=dict(rows=[row]),profile=profile,evaluated_at='2026-11-10T00:00:00+03:00')

class ReviewModel:
    def generate(self, request_id, prompt, **kwargs):
        return dict(answer=json.dumps(dict(text='Проверить срок.',issues=[],score=73)),model='fake')

def test_full_stock_price_uses_presales_settings_and_input_is_unchanged():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(); original=deepcopy((request,snapshot))
    result=analyze_request(request,snapshot,{},ReviewModel())
    line=result['lines'][0]
    assert (line['supply_status'],line['stock_quantity'],line['unit_price'],line['line_total']) == ('full_stock','80','200.00','16000.00')
    assert result['metrics']['product_match_percent']=='100'
    assert result['metrics']['stock_coverage_percent']=='100'
    assert result['score']==73
    assert (request,snapshot)==original
    assert 'unit_cost' not in line['offers'][0]

def test_repeated_sku_never_spends_the_same_stock_twice():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture();request['lines'].append(dict(request['lines'][0],line_id='L2'))
    result=analyze_request(request,snapshot,{})
    assert [x['stock_quantity'] for x in result['lines']]==['80','20']
    assert result['metrics']['stock_coverage_percent']=='62.5'

def test_zero_stock_own_manufacture_and_shared_calendar_ignore_production_cost():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10',free='0');request['lines'].append(dict(request['lines'][0],line_id='L2'))
    snapshot['profile']['production']=[dict(product_id='P',resource_group_id='R',units_per_workday='10',setup_workdays='0',material_ready_at='2026-11-10T00:00:00+03:00',production_unit_cost='999999',currency='USD',evidence='Норма')]
    snapshot['profile']['resources']=[dict(group_id='R',calendar=list(range(7)),default_daily_capacity='1')]
    result=analyze_request(request,snapshot,{})
    assert [x['supply_status'] for x in result['lines']]==['partial_or_manufacture']*2
    assert [x['production_quantity'] for x in result['lines']]==['10','10']
    assert [x['ready_at'][:10] for x in result['lines']]==['2026-11-11','2026-11-12']
    assert [x['unit_price'] for x in result['lines']]==['200.00','200.00']

def test_official_product_unknown_cost_and_norms_are_not_zero_or_rejection():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(free='0');snapshot['profile']['products'][0].pop('manufactured_before');snapshot['stock']['rows'][0]['unit_cost']=None
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert line['supply_status']=='partial_or_manufacture'
    assert line['needs_review']
    assert line['unit_price'] is None
    assert line['production_quantity'] is None
    assert line['ready_at'] is None

def test_kilogram_cost_is_converted_to_requested_piece_and_multiple_skus_stay_separate():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10',free='2.5')
    snapshot['stock']['rows'][0].update(unit='кг',attributes={'weight_per_piece_kg':'.5'})
    snapshot['profile']['products'][0]['attributes']={'weight_per_piece_kg':'.5'}
    snapshot['profile']['products'].append(dict(snapshot['profile']['products'][0],id='P2',sku='B2'))
    snapshot['stock']['rows'].append(dict(snapshot['stock']['rows'][0],sku='B2',unit_cost='200'))
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert [(o['quantity'],o['unit_price'],o['line_total']) for o in line['offers']]==[('5','100.00','500.00'),('5','200.00','1000.00')]
    assert line['unit_price'] is None

def test_model_error_preserves_deterministic_metrics():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture()
    class Bad:
        def generate(self,*a,**k): raise RuntimeError('offline')
    result=analyze_request(request,snapshot,{},Bad())
    assert result['metrics']['stock_coverage_percent']=='100'
    assert result['score'] is None
    assert result['model_error']['code']=='MODEL_ERROR'

def test_manager_analog_is_not_counted_before_confirmation():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture();request['lines'][0]['name']='Болт М14'
    before=analyze_request(request,snapshot,{})
    assert before['metrics']['product_match_percent']=='0'
    assert before['lines'][0]['offers']==[]
    after=analyze_request(request,snapshot,{'approvals':{'L':{'sku':'B','analog_approved':True}}})
    assert after['metrics']['product_match_percent']=='100'
    assert after['lines'][0]['unit_price']=='200.00'
    assert after['lines'][0]['candidates'][0]['differences']

def test_manager_primary_list_and_corrections_do_not_mutate_originals():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture()
    request['lists']=[dict(list_id='other',lines=[dict(request['lines'][0],quantity='200')])]
    result=analyze_request(request,snapshot,{'primary_list_id':'other','line_updates':{'L':{'quantity':'2'}}})
    assert result['lines'][0]['line_total']=='400.00'
    assert request['lists'][0]['lines'][0]['quantity']=='200'
    assert request['lines'][0]['quantity']=='80'

def test_official_catalog_ignores_legacy_false_and_preserves_unknown_stock():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(free='0')
    snapshot['profile']['products'][0]['manufactured_before']=False
    assert analyze_request(request,snapshot,{})['lines'][0]['supply_status']=='partial_or_manufacture'
    snapshot['stock']['rows'][0]['free_quantity']=None
    result=analyze_request(request,snapshot,{})
    assert result['lines'][0]['stock_quantity'] is None
    assert result['lines'][0]['supply_status'] is None
    assert result['metrics']['stock_coverage_incomplete']

def test_partial_calendar_quotes_only_producible_quantity_and_respects_client_date_without_cutoff():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='30',free='0');request['requested_deadline']='2026-11-12'
    snapshot['profile']['production']=[dict(product_id='P',resource_group_id='R',units_per_workday='10',setup_workdays='0',material_ready_at='2026-11-10T00:00:00+03:00',evidence='Норма')]
    snapshot['profile']['resources']=[dict(group_id='R',calendar=list(range(7)),default_daily_capacity='1')]
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert line['production_quantity']=='20'
    assert line['offered_quantity']=='20'
    assert line['line_total']=='4000.00'
    assert line['ready_at'][:10]=='2026-11-12'
    assert 'CAPACITY_INSUFFICIENT' in line['reasons']

def test_repeated_analysis_checkpoint_is_json_serializable_and_stable():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(); saved=[]
    result=analyze_request(request,snapshot,{},ReviewModel(),lambda stage,payload:saved.append((stage,json.loads(json.dumps(payload)))))
    assert [stage for stage,payload in saved]==['match','calculate','ai_review']
    assert saved[-1][1]==result

def test_metrics_use_whole_primary_list_with_confirmed_match_and_weighted_stock():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='100',free='700')
    request['lines']=[dict(request['lines'][0],line_id=str(i),name='Болт М12' if i<8 else 'Гайка М6') for i in range(10)]
    result=analyze_request(request,snapshot,{})
    assert result['metrics']['product_match_percent']=='80'
    assert result['metrics']['stock_coverage_percent']=='70'

def test_large_review_is_batched_without_losing_the_last_line():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='1',free='100')
    request['lines']=[dict(request['lines'][0],line_id=str(i)) for i in range(75)]
    seen=[]
    class BudgetModel:
        def generate(self,request_id,prompt,**kwargs):
            if len(prompt.encode())>64*1024: raise RuntimeError('context limit')
            payload=json.loads(prompt.split('\nВХОД\n',1)[1])
            seen.extend(line['line_id'] for line in payload.get('lines',[]))
            return dict(answer=json.dumps(dict(text='Проверено',issues=[],score=73)),model='fake')
    result=analyze_request(request,snapshot,{},BudgetModel())
    assert result['model_error'] is None
    assert result['score']==73
    assert set(seen)=={str(i) for i in range(75)}

def test_manager_evidence_identifies_changed_fields_and_is_not_duplicated():
    from bervel.modules.presales.analysis import effective_request
    request,_=fixture()
    decisions={'line_updates':{'L':{'quantity':'3'}},'_audit':[{'actor_id':'manager','at':'2026-11-10T12:00:00+03:00','changes':{'line_updates':{'L':{'quantity':'3'}}}}]}
    first=effective_request(request,decisions)
    evidence=first['lines'][0]['evidence']
    assert len(evidence)==1
    assert evidence[0]['source']=='manager'
    assert evidence[0]['actor_id']=='manager'
    assert evidence[0]['fields']==['quantity']
    assert effective_request(first,decisions)['lines'][0]['evidence']==evidence

def test_known_full_allocation_is_not_invalidated_by_unknown_alternative_stock():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture()
    snapshot['profile']['products'].append(dict(snapshot['profile']['products'][0],id='P2',sku='B2'))
    snapshot['stock']['rows'].append(dict(snapshot['stock']['rows'][0],sku='B2',free_quantity=None))
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert line['supply_status']=='full_stock'
    assert line['stock_quantity']=='80'
    assert not line['needs_review']

def test_client_and_deadline_corrections_are_visible_in_effective_request():
    from bervel.modules.presales.analysis import effective_request
    request,_=fixture()
    result=effective_request(request,{'client':'Новый клиент','requested_deadline':'2026-12-01'})
    assert result['client']=='Новый клиент'
    assert result['requested_deadline']=='2026-12-01'
    assert 'client' not in request

def test_model_control_flow_is_not_converted_into_a_business_model_error():
    from bervel.core.errors import CoreError
    from bervel.modules.presales.analysis import analyze_request
    import pytest
    request,snapshot=fixture()
    class Stopped:
        def generate(self,*args,**kwargs): raise CoreError('USER_STOPPED','Stopped')
    with pytest.raises(CoreError,match='USER_STOPPED'):
        analyze_request(request,snapshot,{},Stopped())

def test_resource_dates_use_moscow_and_material_release_uses_local_time():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10',free='0')
    snapshot['evaluated_at']='2026-11-09T21:00:00+00:00'
    snapshot['profile']['production']=[dict(product_id='P',resource_group_id='R',units_per_workday='10',setup_workdays='0',material_ready_at='2026-11-09T21:00:00+00:00',evidence='Норма')]
    snapshot['profile']['resources']=[dict(group_id='R',calendar=list(range(7)),default_daily_capacity='1')]
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert line['ready_at']=='2026-11-11T00:00:00+03:00'

def test_global_stock_coverage_is_unknown_when_one_unit_cannot_be_normalized():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10',free='10')
    request['lines'].append(dict(request['lines'][0],line_id='sets',quantity='1000',unit='комплект'))
    result=analyze_request(request,snapshot,{})
    assert result['lines'][0]['stock_quantity']=='10'
    assert result['metrics']['stock_coverage_percent'] is None
    assert result['metrics']['stock_coverage_incomplete']

def test_confirmed_analog_offers_actual_sku_dimensions_and_an_explicit_substitution():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10')
    request['lines'][0].update(name='Болт М14',requirements=[dict(name='Диаметр резьбы',value='14')])
    decisions={'approvals':{'L':{'sku':'B','analog_approved':True}}}
    line=analyze_request(request,snapshot,decisions)['lines'][0]
    offer=line['offers'][0]
    assert dict(name='Диаметр резьбы',value='12') in offer['requirements']
    assert dict(name='Диаметр резьбы',value='14') not in offer['requirements']
    assert 'аналог' in offer['name'].lower()
    assert offer['substitution']['requested_name']=='Болт М14'
    assert offer['substitution']['offered_name']=='Болт М12'
    assert line['requirements']==[dict(name='Диаметр резьбы',value='14')]

def test_official_product_without_legacy_fields_is_own_product_without_known_norms():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(free='0')
    product=snapshot['profile']['products'][0]
    product.pop('manufactured_before');product.pop('evidence')
    original=deepcopy(snapshot)
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert line['supply_status']=='partial_or_manufacture'
    assert line['needs_review']
    assert line['production_quantity'] is None
    assert line['ready_at'] is None
    assert line['offers']==[]
    assert snapshot==original

def test_official_product_legacy_false_does_not_block_confirmed_production_norm():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10',free='0')
    product=snapshot['profile']['products'][0]
    product['manufactured_before']=False;product.pop('evidence')
    snapshot['profile']['production']=[dict(product_id='P',resource_group_id='R',units_per_workday='10',setup_workdays='0',material_ready_at='2026-11-10T00:00:00+03:00',evidence='Подтверждённая норма')]
    snapshot['profile']['resources']=[dict(group_id='R',calendar=list(range(7)),default_daily_capacity='1')]
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert line['supply_status']=='partial_or_manufacture'
    assert line['production_quantity']=='10'
    assert line['ready_at']=='2026-11-11T00:00:00+03:00'
    assert line['line_total']=='2000.00'

def test_experimental_catalog_and_an_empty_capability_do_not_prove_own_manufacture():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(free='0')
    snapshot['profile']['products']=[]
    snapshot['profile']['include_experimental']=True
    snapshot['profile']['experimental_products']=[dict(id='P',name='Болт М12',unit='шт',attributes={},enabled=True)]
    snapshot['profile']['production']=[dict(product_id='P',resource_group_id='R',units_per_workday=None,setup_workdays='0',material_ready_at=None)]
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert line['supply_status'] is None
    assert line['needs_review']
    assert line['production_quantity'] is None
    assert line['ready_at'] is None
    assert line['offers']==[]

def test_manager_primary_choice_resolves_a_complete_source_tie_without_erasing_warnings():
    from bervel.modules.presales.analysis import analyze_request, effective_request
    request,snapshot=fixture(quantity='10',free='10')
    first=dict(request['lines'][0]);second=dict(first,line_id='L2',name='Болт М14')
    request.update(lines=[],complete=False,source_complete=True,selection_complete=False,
                   warnings=['Различаются списки'],lists=[dict(list_id='A',lines=[first],complete=True),dict(list_id='B',lines=[second],complete=True)])
    effective=effective_request(request,{'primary_list_id':'A'})
    assert effective['complete']
    assert effective['selection_complete']
    assert effective['warnings']==['Различаются списки']
    assert request['complete'] is False
    result=analyze_request(request,snapshot,{'primary_list_id':'A'})
    assert result['metrics']['stock_coverage_percent']=='100'
    assert not result['metrics']['stock_coverage_incomplete']

def test_manager_primary_choice_does_not_claim_an_unread_source_is_complete():
    from bervel.modules.presales.analysis import analyze_request, effective_request
    request,snapshot=fixture(quantity='10',free='10')
    request.update(complete=False,source_complete=False,selection_complete=False,
                   lists=[dict(list_id='A',lines=request['lines'],complete=True)])
    assert not effective_request(request,{'primary_list_id':'A'})['complete']
    result=analyze_request(request,snapshot,{'primary_list_id':'A'})
    assert result['metrics']['stock_coverage_percent'] is None
    assert result['metrics']['stock_coverage_incomplete']

def test_legacy_primary_choice_requires_explicit_completeness_for_every_source_list():
    from bervel.modules.presales.analysis import effective_request
    request,_=fixture()
    request.update(complete=False,lists=[dict(list_id='A',lines=request['lines'],complete=True),dict(list_id='B',lines=request['lines'],complete=True)])
    assert effective_request(request,{'primary_list_id':'A'})['complete']
    request['lists'][1].pop('complete')
    assert not effective_request(request,{'primary_list_id':'A'})['complete']

def test_known_technical_conflict_with_every_official_sku_is_not_in_the_confirmed_catalog():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10')
    request['lines'][0]['name']='Гайка М12'
    result=analyze_request(request,snapshot,{})
    line=result['lines'][0]
    assert line['supply_status']=='not_manufactured'
    assert 'NO_CONFIRMED_CATALOG_EXECUTION' in line['reasons']
    assert line['offers']==[]
    assert line['offered_quantity']=='0'

def test_unknown_dimensions_do_not_prove_absence_from_the_confirmed_catalog():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10')
    request['lines'][0]['requirements']=[dict(name='Длина',value='40')]
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert line['supply_status'] is None
    assert line['needs_review']
    assert 'NO_CONFIRMED_CATALOG_EXECUTION' not in line['reasons']

def test_incomplete_source_and_unrecognized_product_names_are_not_negative_catalog_proofs():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10')
    request['lines'][0]['name']='Гайка М12'
    request['complete']=False
    assert analyze_request(request,snapshot,{})['lines'][0]['supply_status'] is None
    request['complete']=True
    request['lines'][0]['name']='Шланг напорный'
    assert analyze_request(request,snapshot,{})['lines'][0]['supply_status'] is None

def test_one_unresolved_official_candidate_blocks_a_negative_catalog_conclusion():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10')
    request['lines'][0]['name']='Гайка М12'
    snapshot['profile']['products'].append(dict(id='unknown',sku='U',name='Неописанная деталь',unit='шт',attributes={},enabled=True))
    line=analyze_request(request,snapshot,{})['lines'][0]
    assert line['supply_status'] is None
    assert 'NO_CONFIRMED_CATALOG_EXECUTION' not in line['reasons']
