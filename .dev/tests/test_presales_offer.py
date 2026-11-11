import json
import pytest


def money_revision(currency,price,quantity):
    return {'request_id':'00000000-0000-0000-0000-000000000001','revision_id':'revision','revision_number':3,
        'evaluated_at':'2026-11-10T12:00:00Z','extracted':{},'analysis':{'lines':[
            {'line_id':'1','name':'Изделие','quantity':quantity,'unit':'кг','supply_status':'full_stock','needs_review':False,
             'offers':[{'sku':'sku','name':'Изделие','quantity':quantity,'unit':'кг','currency':currency,'unit_price':price,'line_total':'stale','requirements':[]}]}]}}


def test_offer_splits_confirmed_partial_rows_and_computes_totals_without_internals():
    from bervel.modules.presales.offer import build_offer
    revision={'request_id':'request','revision_id':'revision','revision_number':2,'evaluated_at':'2026-11-10T12:00:00Z','extracted':{'client':'Клиент','lines':[]},'analysis':{'review':{'text':'SECRET_AI'},'lines':[
      {'line_id':'1','name':'Болт','quantity':'10','unit':'шт','supply_status':'partial_or_manufacture','unit_cost':'SECRET_COST','margin_percent':'SECRET_MARGIN','needs_review':False,'offered_quantity':'4','offers':[{'sku':'sku','name':'Болт','quantity':'4','unit':'шт','currency':'RUB','unit_price':'12.25','line_total':'999','ready_at':None,'requirements':[]}]},
      {'line_id':'2','name':'Гайка','quantity':'1','supply_status':'special_order','needs_review':True,'offers':[]}]}}
    offer=build_offer(revision,{'seller_contacts':'info@bervel.ru','offer_terms':'Доставка по согласованию'})
    assert offer['totals'] == {'RUB':'49.00'}
    assert offer['lines'][0]['quantity'] == '4'
    assert offer['clarifications'] and offer['unsupported']
    assert not any(secret in json.dumps(offer) for secret in ['SECRET_COST','SECRET_MARGIN','SECRET_AI','unit_cost','margin_percent'])


@pytest.mark.parametrize('currency,price,quantity,total',[('JPY','333','1.5','500'),('RUB','12.345','1.25','15.43'),('RUB','12.25','1.333','16.33')])
def test_preserves_analysis_unit_price_and_uses_currency_quantum_for_total(currency,price,quantity,total):
    from bervel.modules.presales.offer import build_offer
    revision=money_revision(currency,price,quantity)
    offer=build_offer(revision,{})
    assert offer['lines'][0]['unit_price']==price
    assert offer['lines'][0]['line_total']==total
    assert offer['totals']=={currency:total}
    assert offer['number']=='PS-00000000-0000-0000-0000-000000000001-R3'


def test_unknown_currency_does_not_get_assumed_precision_or_price():
    from bervel.modules.presales.offer import build_offer
    result=build_offer(money_revision('UNKNOWN','12.345','1.25'),{})
    assert result['lines']==[] and result['totals']=={} and result['clarifications']
