import json
from test_presales_analysis import fixture

class SpecialModel:
    def generate(self,request_id,prompt,**kwargs):
        if prompt.startswith('SPECIAL_ORDER.'):
            return dict(answer=json.dumps(dict(recommended=True,sku='B',reason='Близкий диаметр',evidence=[dict(field='product',value='Болт М12')],unit_price='1')),model='fake')
        return dict(answer=json.dumps(dict(text='Нужна адаптация',issues=[],score=61)),model='fake')

def test_special_needs_manager_approvals_and_price_is_computed_not_taken_from_model():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10');request['lines'][0]['name']='Болт М14'
    first=analyze_request(request,snapshot,{},SpecialModel())['lines'][0]
    assert first['supply_status']=='special_order'
    assert first['offers']==[] and first['unit_price'] is None
    decisions={'approvals':{'L':{'sku':'B','adaptation_approved':True,'cost_approved':True}}}
    approved=analyze_request(request,snapshot,decisions,SpecialModel())['lines'][0]
    assert approved['unit_price']=='333.34'
    assert approved['margin_percent']=='70'

def test_client_price_below_seventy_percent_margin_blocks_special_quote():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10');request['lines'][0].update(name='Болт М14',client_unit_price='300',currency='RUB')
    decisions={'approvals':{'L':{'sku':'B','adaptation_approved':True,'cost_approved':True}}}
    line=analyze_request(request,snapshot,decisions,SpecialModel())['lines'][0]
    assert line['supply_status']=='special_order'
    assert not line['offers']
    assert 'CLIENT_PRICE_BELOW_MINIMUM_MARGIN' in line['reasons']

def test_approved_specials_share_one_stock_remainder():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='80',free='100');request['lines'][0]['name']='Болт М14'
    request['lines'].append(dict(request['lines'][0],line_id='L2'))
    decisions={'approvals':{key:{'sku':'B','adaptation_approved':True,'cost_approved':True} for key in ('L','L2')}}
    result=analyze_request(request,snapshot,decisions,SpecialModel())
    assert [line['offered_quantity'] for line in result['lines']]==['80','20']

def test_forged_model_evidence_cannot_create_special_order():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture();request['lines'][0]['name']='Болт М14'
    class Forged(SpecialModel):
        def generate(self,request_id,prompt,**kwargs):
            if prompt.startswith('SPECIAL_ORDER.'):
                return dict(answer=json.dumps(dict(recommended=True,sku='B',reason='Рекомендуется',evidence=[dict(field='product',value='Болт М14')])),model='fake')
            return super().generate(request_id,prompt,**kwargs)
    result=analyze_request(request,snapshot,{},Forged())
    assert result['lines'][0]['supply_status'] is None
    assert result['lines'][0]['offers']==[]
    assert result['model_error']['code']=='MODEL_INVALID_OUTPUT'

def test_approved_special_can_use_confirmed_own_production_with_zero_stock():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10',free='0')
    request['lines'][0].update(name='Болт М14',client_unit_price='400',currency='RUB')
    snapshot['profile']['production']=[dict(product_id='P',resource_group_id='R',units_per_workday='10',setup_workdays='0',material_ready_at='2026-11-10T00:00:00+03:00',evidence='Норма')]
    snapshot['profile']['resources']=[dict(group_id='R',calendar=list(range(7)),default_daily_capacity='1')]
    decisions={'approvals':{'L':{'sku':'B','adaptation_approved':True,'cost_approved':True}}}
    line=analyze_request(request,snapshot,decisions,SpecialModel())['lines'][0]
    assert line['production_quantity']=='10'
    assert line['offered_quantity']=='10'
    assert line['ready_at']=='2026-11-11T00:00:00+03:00'
    assert line['unit_price']=='333.34'
    assert line['offers'][0]['name']=='Болт М14'
    assert not line['needs_review']

def test_missing_client_price_does_not_prove_special_commercial_terms():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture();request['lines'][0]['name']='Болт М14'
    decisions={'approvals':{'L':{'sku':'B','adaptation_approved':True,'cost_approved':True}}}
    line=analyze_request(request,snapshot,decisions,SpecialModel())['lines'][0]
    assert line['unit_price']=='333.34'
    assert line['needs_review']
    assert 'CLIENT_PRICE_UNKNOWN' in line['reasons']

def test_special_model_control_flow_propagates():
    from bervel.core.errors import CoreError
    from bervel.modules.presales.analysis import analyze_request
    import pytest
    request,snapshot=fixture();request['lines'][0]['name']='Болт М14'
    class Unknown:
        def generate(self,*args,**kwargs): raise CoreError('MODEL_OUTCOME_UNKNOWN','Uncertain')
    with pytest.raises(CoreError,match='MODEL_OUTCOME_UNKNOWN'):
        analyze_request(request,snapshot,{},Unknown())

def test_special_catalog_is_processed_in_batches_including_the_last_candidate():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture();request['lines'][0]['name']='Болт М14'
    product=snapshot['profile']['products'][0];row=snapshot['stock']['rows'][0]
    snapshot['profile']['products']=[dict(product,id=f'P{i}',sku=f'B{i}',attributes={'description':'Материал '+('x'*1000)}) for i in range(40)]
    snapshot['stock']['rows']=[dict(row,sku=f'B{i}') for i in range(40)]
    class BudgetSpecial(SpecialModel):
        def generate(self,request_id,prompt,**kwargs):
            if len(prompt.encode())>64*1024: raise RuntimeError('context limit')
            if not prompt.startswith('SPECIAL_ORDER.'): return super().generate(request_id,prompt,**kwargs)
            payload=json.loads(prompt.split('\nВХОД\n',1)[1])
            candidate=next((c for c in payload['candidates'] if c['sku']=='B39'),None)
            result=dict(recommended=False,sku=None,reason='',evidence=[])
            if candidate: result.update(recommended=True,sku='B39',reason='Подходит для адаптации',evidence=[dict(field='product',value='Болт М12')])
            return dict(answer=json.dumps(result),model='fake')
    result=analyze_request(request,snapshot,{},BudgetSpecial())
    assert result['model_error'] is None
    assert result['lines'][0]['special_order']['sku']=='B39'

def test_partial_special_stock_does_not_turn_unknown_manufacturing_into_zero():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10',free='5')
    request['lines'][0].update(name='Болт М14',client_unit_price='400',currency='RUB')
    decisions={'approvals':{'L':{'sku':'B','adaptation_approved':True,'cost_approved':True}}}
    line=analyze_request(request,snapshot,decisions,SpecialModel())['lines'][0]
    assert line['offered_quantity']=='5'
    assert line['production_quantity'] is None
    assert line['needs_review']
    assert 'PRODUCTION_NORMS_UNKNOWN' in line['reasons']

def test_confirmed_adaptation_keeps_client_execution_separate_from_an_unchanged_analog():
    from bervel.modules.presales.analysis import analyze_request
    request,snapshot=fixture(quantity='10')
    request['lines'][0].update(name='Болт М14',requirements=[dict(name='Диаметр резьбы',value='14')],client_unit_price='400',currency='RUB')
    decisions={'approvals':{'L':{'sku':'B','adaptation_approved':True,'cost_approved':True}}}
    offer=analyze_request(request,snapshot,decisions,SpecialModel())['lines'][0]['offers'][0]
    assert offer['name']=='Болт М14'
    assert offer['requirements']==[dict(name='Диаметр резьбы',value='14')]
    assert offer['adaptation_from']=='Болт М12'
    assert 'substitution' not in offer
