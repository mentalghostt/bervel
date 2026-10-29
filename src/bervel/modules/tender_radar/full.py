"""Full pipeline rules and report1.3; no database, HTTP or model clients."""
from datetime import datetime,timedelta
from decimal import Decimal,InvalidOperation
from collections import Counter
import re
from bervel.core.errors import CoreError
from bervel.modules.tender_radar.stock import percentage,decimal_text,QUANTITY_PATTERN,unit,number
from bervel.modules.tender_radar.preselection import preselect
from bervel.modules.tender_radar.analysis import make_report
from bervel.modules.tender_radar.economics import economics_check


def validate_filters(value):
    defaults=dict(min_price=None,max_price=None,min_days_remaining=0,focus='',max_shortage_percent='0',min_margin_percent='15')
    try:
        if not isinstance(value,dict) or set(value)-set(defaults):raise ValueError()
        result=defaults|value
        for k in ('min_price','max_price'):
            raw=result[k]
            if raw is not None:
                if isinstance(raw,bool):raise ValueError()
                v=Decimal(str(raw))
                if not v.is_finite() or v<0 or len(v.as_tuple().digits)>30 or abs(v.as_tuple().exponent)>12:raise ValueError()
                result[k]=decimal_text(v)
        if result['min_price'] is not None and result['max_price'] is not None and Decimal(result['min_price'])>Decimal(result['max_price']):raise ValueError()
        if type(result['min_days_remaining'])!=int or not 0<=result['min_days_remaining']<=3650:raise ValueError()
        if not isinstance(result['focus'],str) or len(result['focus'])>1000:raise ValueError()
        for k in ('max_shortage_percent','min_margin_percent'):result[k]=decimal_text(percentage(result[k]))
        return result
    except (ValueError,InvalidOperation,TypeError):
        raise CoreError('VALIDATION_ERROR','Проверьте фильтры: цены от0 и от≤до, дни0–3650, проценты0–100, фокус до1000символов.') from None


def filter_rows(rows,sources,now,filters):
    candidates,prefiltered=preselect(rows,sources,now)
    accepted=[]
    for row in candidates:
        reason=None
        published=datetime.fromisoformat(row['published_at']) if row.get('published_at') else None
        deadline=datetime.fromisoformat(row['deadline_at']) if row.get('deadline_at') else None
        if published and published>now:reason='Закупка опубликована после даты анализа.'
        if deadline and deadline-now<timedelta(days=filters['min_days_remaining']):reason='Запас дней до окончания подачи меньше выбранного.'
        bounded=filters['min_price'] is not None or filters['max_price'] is not None
        if bounded and row.get('currency','').upper()!='RUB':reason='Фильтр цены задан в RUB; валюта закупки другая или неизвестна.'
        if bounded and row.get('price') is None:reason='НМЦ неизвестна; ценовой фильтр проверить нельзя.'
        if row.get('price') is not None and row.get('currency','').upper()=='RUB':
            amount=Decimal(row['price'])
            if filters['min_price'] is not None and amount<Decimal(filters['min_price']):reason='НМЦ ниже минимальной цены.'
            if filters['max_price'] is not None and amount>Decimal(filters['max_price']):reason='НМЦ выше максимальной цены.'
        if reason:prefiltered[row['id']]={'stage':'eligibility','reason':reason}
        else:accepted.append(row)
    return accepted,prefiltered


def full_report(rows,sources,assessments,now,*,prefiltered,stock,economics,filters,errors,import_data,complete):
    errors_by_id={key:error for error in errors for key in error['ids']}
    accepted={i['id']:i for i in assessments}
    skipped=dict(prefiltered)
    for row in rows:
        if row['id'] not in accepted and row['id'] not in skipped:
            error=errors_by_id.get(row['id'])
            skipped[row['id']]={'stage':'meaning','reason':error['message'] if error else 'Порция ещё не проверена моделью.'}
    report=make_report(rows,sources,assessments,now,prefiltered=skipped,stock=stock,allowed=percentage(filters['max_shortage_percent']))
    report['schema_version']='1.3.0';report['economics_kind']=economics['kind']
    catalogue={i['sku']:i for i in stock['items']}
    for row,item in zip(rows,report['items']):
        item['origins']=row.get('origins',[row.get('origin')])
        item['mock']=item['demo']=bool(item['demo'] or economics['kind']=='mock')
        item['economics_check']=None
        if row['id'] not in accepted and row['id'] not in prefiltered:
            item.update(decision='not_analysed',decision_stage='meaning')
        if item.get('fit')=='relevant':
            technical=[]
            # A model completeness flag cannot overrule an objectively omitted
            # quantity line (including repeated identical demand lines).
            demand=Counter()
            for match in re.finditer(QUANTITY_PATTERN,row.get('title','')+'\n'+row.get('description',''),re.I):
                if not unit(match[2]):continue
                try:demand[(number(match[1]),unit(match[2]))]+=1
                except ValueError:technical.append('Количество в исходной строке превышает допустимую точность; требуется проверка.')
            represented=Counter()
            for position in item['positions']:
                try:
                    if unit(position['unit']):represented[(number(position['quantity']),unit(position['unit']))]+=1
                except ValueError:pass
            if demand-represented:technical.append('Не все количественные строки тендера представлены в проверенных позициях.')
            for position in item['positions']:
                product=catalogue.get(position['sku'])
                if product is None:continue
                required=('standard','thread','strength','coating')
                if re.search('болт|bolt',product['product'],re.I):required+=('length_mm',)
                if any(not product['specs'].get(k) for k in required):technical.append('В каталоге не заполнены обязательные характеристики SKU '+product['sku']+'.')
            check=economics_check(row,item['stock_check'] or {'status':'unknown','checks':[]},economics,percentage(filters['min_margin_percent']),now)
            item['economics_check']=check;item['economics']=check['status']
            item['production_readiness']='ready' if check['status']=='pass' else 'insufficient' if check['status']=='fail' else 'unknown'
            if item['decision']!='excluded':
                if check['status']=='fail':item.update(decision='excluded',decision_stage='economics',reason=' '.join(check['reasons']))
                elif check['status']=='unknown':item.update(decision='needs_review',decision_stage='economics',reason=' '.join(check['reasons']))
                elif technical:item.update(decision='needs_review',decision_stage='meaning',reason=' '.join(technical))
                elif accepted[row['id']]['risks']:
                    item.update(decision='needs_review',decision_stage='meaning',
                        reason='Не подтверждены существенные условия тендера. '+' '.join(accepted[row['id']]['risks']))
                elif item['decision']=='take_in_work':
                    item.update(technical_readiness='ready',participation_ready=not item['mock'],reason='Характеристики, склад, сроки, бюджет и маржа соответствуют выбранным входам.')
                    # This branch has no eligibility guard or model risks. Only
                    # make_report's legacy stock/economics warning is resolved.
                    item['risks']=list(accepted[row['id']]['risks'])
        if item['mock']:item['risks'].append('МОК: расчёт основан на демонстрационных данных и не подтверждает факты производства.')
    counts={k:sum(i['decision']==k for i in report['items']) for k in ('take_in_work','needs_review','excluded','not_analysed')}
    report['counts']={'total':len(rows),'model_analysed':len(assessments),**counts}
    duplicates=import_data['duplicate_rows']
    report['coverage']=dict(input_rows=len(rows)+duplicates,unique_tenders=len({(r['number'],r['source_url']) for r in rows}),duplicate_rows=duplicates,
        skipped_rows=import_data['skipped_rows'],accounted_rows=sum(len(r.get('origins',[r.get('origin')])) for r in rows),
        model_failed_rows=sum(len(e['ids']) for e in errors),complete=complete)
    report['portion_errors']=errors;report['analysis_complete']=complete
    report['notice']='Полный проход: сроки/цена → профиль склада → смысл/позиции → свободный остаток → экономика/мощности. Каждый тендер независим; резерв не создаётся. Выручка рассчитана по ценам продажи, НМЦ — только потолок бюджета.'
    if stock['kind']=='mock' or economics['kind']=='mock':report['notice']+=' МОК: использованы демонстрационные исходные данные.'
    if not complete:report['notice']+=' АНАЛИЗ НЕ ЗАВЕРШЁН: непроверенные порции видны отдельно.'
    return report
