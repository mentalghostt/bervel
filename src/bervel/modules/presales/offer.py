"""Customer-safe structured quotation built only from confirmed offered quantities."""
from decimal import Decimal, InvalidOperation, localcontext

from .pricing import CURRENCY_QUANTA, line_amount


def _decimal(value):
    if value is None: return None
    try: result=Decimal(str(value))
    except InvalidOperation: return None
    return result if result.is_finite() and result>=0 else None


def build_offer(revision: dict, settings: dict) -> dict:
    lines=[]; totals={}; clarifications=[]; unsupported=[]
    for source in revision.get('analysis',{}).get('lines',[]):
        line_id=source['line_id']; name=source['name']; offered=Decimal(0)
        status=source.get('supply_status')
        permitted=status in {'full_stock','partial_or_manufacture'} or (status=='special_order' and not source.get('needs_review',True))
        for offer in source.get('offers',[]) if permitted else []:
            quantity=_decimal(offer.get('quantity')); price=_decimal(offer.get('unit_price')); currency=offer.get('currency')
            if quantity is None or quantity<=0 or price is None or currency not in CURRENCY_QUANTA:
                clarifications.append({'line_id':line_id,'name':name,'message':'Количество, цена или валюта требуют уточнения.'}); continue
            total=line_amount(unit_price=price,quantity=quantity,currency=currency)
            # Explicit projection prevents analysis, cost and margin from entering the document.
            lines.append({'line_id':line_id,'sku':offer.get('sku'),'name':offer.get('name') or name,
                'quantity':format(quantity,'f'),'unit':offer.get('unit'),'currency':str(currency),
                'unit_price':str(offer['unit_price']),'line_total':format(total,'f'),
                'ready_at':offer.get('ready_at'),'requirements':[{'name':r['name'],'value':r['value']} for r in offer.get('requirements',[])]})
            with localcontext() as context:
                context.prec=100
                totals[currency]=totals.get(currency,Decimal(0))+total; offered+=quantity
            if not offer.get('ready_at'): clarifications.append({'line_id':line_id,'name':name,'message':'Срок готовности уточняется.'})
        required=_decimal(source.get('quantity'))
        if required is not None and offered<required:
            unsupported.append({'line_id':line_id,'name':name,'quantity':format(required-offered,'f'),
                'unit':source.get('unit'),'message':'Оставшееся количество не включено в ценовую часть предложения.'})
        elif not offered:
            unsupported.append({'line_id':line_id,'name':name,'quantity':source.get('quantity'),'unit':source.get('unit'),
                'message':'Исполнение и цена требуют согласования.'})
        if source.get('needs_review'):
            clarifications.append({'line_id':line_id,'name':name,'message':'Характеристики или условия исполнения требуют согласования.'})
    extracted=revision.get('extracted',{})
    if not extracted.get('complete',True):
        clarifications.append({'name':'Состав заявки','message':'Состав и требования заявки требуют сверки с клиентом.'})
    return {'number':f"PS-{revision['request_id']}-R{revision['revision_number']}",
        'date':str(revision['evaluated_at'])[:10],'client':extracted.get('client'),
        'seller_contacts':settings.get('seller_contacts',''),'offer_terms':settings.get('offer_terms',''),
        'lines':lines,'totals':{currency:format(value,'f') for currency,value in totals.items()},
        'clarifications':clarifications,'unsupported':unsupported}
