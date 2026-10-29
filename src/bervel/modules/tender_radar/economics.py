"""Decimal tender economics over explicit allocated SKU costs and capacity."""
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, localcontext
import re
from bervel.modules.tender_radar.stock import decimal_text


def delivery_days(row,now):
    text=row.get('description','')+'\n'+row.get('title','')
    relative=re.findall(r'поставк\w*[^.;\n]{0,40}?в течение\s+(\d+)\s+(?:календарных\s+)?дн',text,re.I)
    dates=re.findall(r'поставк\w*[^.;\n]{0,30}?до\s+(\d{2}\.\d{2}\.\d{4})',text,re.I)
    values={Decimal(v) for v in relative}
    for raw in dates:
        try:values.add(Decimal((datetime.strptime(raw,'%d.%m.%Y').date()-now.date()).days))
        except ValueError:return None
    return values.pop() if len(values)==1 else None


def economics_check(row,stock_check,data,min_margin,now):
    result=dict(status='unknown',currency=None,revenue=None,total_cost=None,profit=None,margin_percent=None,
        budget=row.get('price'),required_days=None,available_days=None,reasons=[],lines=[])
    reasons=result['reasons'];items={i['sku']:i for i in data['items']}
    days=delivery_days(row,now)
    if days is None:reasons.append('Неизвестен или противоречив срок поставки; срок подачи не является сроком поставки.')
    else:result['available_days']=decimal_text(days)
    checks=stock_check['checks']
    if stock_check['status']=='unknown' or not checks:reasons.append('Нет полной подтверждённой потребности по SKU.')
    currencies={items[c['sku']]['currency'] for c in checks if c['sku'] in items}
    currency=row.get('currency','').upper()
    if len(currencies)!=1 or currency not in currencies:
        reasons.append('Валюты тендера и экономики не совпадают либо неизвестны; курса нет.')
    else:result['currency']=currency
    required_fields=('sale_price','unit_cost','fixed_cost','logistics_cost','packaging_per_unit','capacity_per_day','production_lead_days','delivery_lead_days')
    revenue=Decimal(0);cost=Decimal(0);required_days=Decimal(0)
    with localcontext() as ctx:
        ctx.prec=60
        for check in checks:
            item=items.get(check['sku'])
            if item is None:
                reasons.append('Нет экономики SKU '+check['sku']);continue
            if item['unit']!=check['unit']:
                reasons.append('Единицы экономики и потребности не совпадают: '+check['sku']);continue
            if any(item.get(k) in (None,'') for k in required_fields):
                reasons.append('Неизвестны цена, расходы или мощность SKU '+check['sku']);continue
            v={k:Decimal(item[k]) for k in required_fields}
            if v['capacity_per_day']<=0:
                reasons.append('Нет положительной доступной мощности SKU '+check['sku']);continue
            count=Decimal(check['required']);shortage=Decimal(check['shortage'])
            line_revenue=count*v['sale_price']
            line_cost=count*(v['unit_cost']+v['packaging_per_unit'])+v['fixed_cost']+v['logistics_cost']
            revenue+=line_revenue;cost+=line_cost
            manufacture=(shortage/v['capacity_per_day']).to_integral_value(rounding=ROUND_CEILING)+v['production_lead_days'] if shortage else Decimal(0)
            required_days=max(required_days,manufacture+v['delivery_lead_days'])
            result['lines'].append(dict(sku=check['sku'],quantity=decimal_text(count),sale_price=item['sale_price'],
                revenue=decimal_text(line_revenue),cost=decimal_text(line_cost),source_row=item['row']))
        if len(result['lines'])==len(checks) and checks and result['currency']:
            profit=revenue-cost
            result.update(revenue=decimal_text(revenue),total_cost=decimal_text(cost),profit=decimal_text(profit),required_days=decimal_text(required_days))
            if revenue>0:result['margin_percent']=decimal_text((profit*100/revenue).quantize(Decimal('0.0001')))
            else:reasons.append('Выручка равна нулю, маржа не определена.')
        failures=[]
        if result['revenue'] is not None:
            if row.get('price') is None:reasons.append('Бюджет закупки неизвестен.')
            elif revenue>Decimal(row['price']):failures.append('Цена продажи превышает бюджет закупки.')
            if revenue>0 and (revenue-cost)*100<revenue*min_margin:failures.append('Маржа ниже выбранного порога.')
            if days is not None and required_days>days:failures.append('Производство и доставка не укладываются в срок поставки.')
        # A proven failure is decisive even if another input is unknown.
        result['status']='fail' if failures else 'unknown' if reasons else 'pass'
        reasons.extend(failures)
    return result
