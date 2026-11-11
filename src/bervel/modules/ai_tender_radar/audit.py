"""Repeat calculations against saved inputs, with independent invariant checks."""
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import json

from bervel.core.errors import CoreError
from bervel.core.model_gateway import ModelClient
from bervel.modules.ai_tender_radar.calculation import calculate_analysis
from bervel.modules.ai_tender_radar.completeness import check_completeness
from bervel.modules.ai_tender_radar.documents import LEGACY_PROMPT_VERSION, enrich_tender
from bervel.modules.ai_tender_radar.matching import MatchingResult, PositionMatches, match_positions
from bervel.modules.ai_tender_radar.production import BASELINE_PRODUCTION_RULE_VERSION, PRODUCTION_RULE_VERSION, evaluate_fulfillment
from bervel.modules.ai_tender_radar.scoring import score_assessment
from bervel.modules.company_settings.models import production_products

RULE_VERSIONS = {'audit':'run-audit-v1','invariants':'audit-invariants-v1','calculation':1,
                 'production':PRODUCTION_RULE_VERSION,
                 'production_supported':[1,2,BASELINE_PRODUCTION_RULE_VERSION,PRODUCTION_RULE_VERSION],
                 'score':'score-v2','score_supported':['score-v1','score-v2']}


def _number(value) -> Decimal | None:
    if not isinstance(value,(str,int)) or isinstance(value,bool) or len(str(value))>512:
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _finding(card: dict,stage: str,reason: str,severity: str = 'error',**details) -> dict:
    return {'analysis_id':str(card.get('analysis_id','unknown')),'stage':stage,'severity':severity,'reason':reason,**details}


def check_invariants(card: dict,stock: dict,profile: dict) -> list[dict]:
    with localcontext() as context:
        context.prec = 100
        return _check_invariants(card, stock, profile)


def _check_invariants(card: dict,stock: dict,profile: dict) -> list[dict]:
    """Check certificates directly; do not invoke the allocator or production scheduler."""
    findings = []
    analysis,plan = card.get('analysis') or {},card.get('fulfillment') or {}
    production_version = plan.get('version', 1)
    legacy = production_version < BASELINE_PRODUCTION_RULE_VERSION
    warehouse = {r['sku']:r for r in stock.get('rows',[])}
    spent = defaultdict(lambda:Decimal(0))
    for line in analysis.get('positions',[]):
        required,covered,shortage = (_number(line.get(k)) for k in ('required','covered','shortage'))
        if required is not None and covered is not None and (required < 0 or covered < 0 or covered > required):
            findings.append(_finding(card,'stock','COVERAGE_OUT_OF_RANGE',position_id=line.get('id')))
        if required is not None and covered is not None and shortage is not None and required-covered != shortage:
            findings.append(_finding(card,'stock','SHORTAGE_ARITHMETIC_CONFLICT',position_id=line.get('id')))
        total = Decimal(0)
        for allocation in line.get('allocations',[]):
            amount,quantity = _number(allocation.get('stock_quantity')),_number(allocation.get('position_quantity'))
            if amount is None or quantity is None or amount < 0 or quantity < 0:
                findings.append(_finding(card,'stock','STOCK_ALLOCATION_INVALID',position_id=line.get('id')))
                continue
            spent[allocation.get('sku')] += amount
            total += quantity
        if covered is not None and total != covered:
            findings.append(_finding(card,'stock','STOCK_ALLOCATION_SUM_CONFLICT',position_id=line.get('id')))
    for sku,amount in spent.items():
        available = _number((warehouse.get(sku) or {}).get('free_quantity'))
        if available is None:
            findings.append(_finding(card,'stock','STOCK_AVAILABILITY_UNKNOWN','warning',sku=sku))
        elif amount > available:
            findings.append(_finding(card,'stock','STOCK_OVERALLOCATED',sku=sku,allocated=str(amount),available=str(available)))
    # The fulfillment certificate has its own allocations. Check it separately, never add it twice to analysis.
    certified = defaultdict(lambda:Decimal(0))
    for item in plan.get('stock_allocations',[]):
        amount = _number(item.get('stock_quantity'))
        if amount is not None and amount >= 0:
            certified[item.get('sku')] += amount
    for sku,amount in certified.items():
        available = _number((warehouse.get(sku) or {}).get('free_quantity'))
        if available is not None and amount>available and spent[sku]<=available:
            findings.append(_finding(card,'stock','STOCK_OVERALLOCATED',sku=sku,allocated=str(amount),available=str(available),certificate='fulfillment'))
    resources = {r['group_id']:r for r in profile.get('resources',[])}
    busy = defaultdict(lambda:Decimal(0))
    scheduled = defaultdict(lambda:Decimal(0))
    for item in plan.get('schedule',[]):
        amount = _number(item.get('workdays'))
        try:
            day = date.fromisoformat(item['date'])
        except (ValueError,TypeError,KeyError):
            findings.append(_finding(card,'production','SCHEDULE_DATE_INVALID'))
            continue
        if amount is None or amount<0:
            findings.append(_finding(card,'production','SCHEDULE_ALLOCATION_INVALID'))
            continue
        busy[(item.get('resource_group_id'),day)] += amount
        scheduled[item.get('position_id')] += amount
    for (group,day),amount in busy.items():
        resource = resources.get(group) or {}
        calendar = resource.get('calendar')
        capacity = None
        if not legacy and resource.get('enabled') is False:
            capacity = Decimal(0)
        elif isinstance(calendar, list):
            daily = (resource.get('free_capacity_by_day') or {}) if legacy else {}
            capacity = (_number(daily.get(day.isoformat(),resource.get('default_daily_capacity')))
                        if day.weekday() in calendar else Decimal(0))
        if capacity is None:
            findings.append(_finding(card,'production','RESOURCE_CAPACITY_UNKNOWN','warning',resource_group_id=group,date=day.isoformat()))
        elif amount>capacity or capacity<0 or capacity>1:
            findings.append(_finding(card,'production','RESOURCE_OVERALLOCATED',resource_group_id=group,date=day.isoformat(),allocated=str(amount),available=str(capacity)))
    products = ({p.get('sku') or 'CAT:'+str(p.get('id')):p for p in profile.get('products',[])}
                if legacy else production_products(profile))
    for item in plan.get('production_allocations',[]):
        work = _number(item.get('workdays'))
        if work is not None and plan.get('status') == 'feasible' and scheduled[item.get('position_id')] != work:
            findings.append(_finding(card,'production','SCHEDULE_WORK_CONFLICT',position_id=item.get('position_id')))
        product = products.get(item.get('sku')) or {}
        eligible = (bool(product.get('enabled')) if production_version >= 2
                    else product.get('manufactured_before') is True and bool(product.get('evidence')))
        if not eligible:
            findings.append(_finding(card,'production','UNCONFIRMED_PRODUCTION',position_id=item.get('position_id'),sku=item.get('sku')))
    for stage,economics in [('stock',analysis.get('economics') or {}),('production',plan.get('full_economics') or {})]:
        revenue,cost,profit = (_number(economics.get(k)) for k in ('revenue','cost','profit'))
        if all(n is not None for n in (revenue,cost,profit)) and revenue-cost != profit:
            findings.append(_finding(card,stage,'PROFIT_ARITHMETIC_CONFLICT',persisted=economics))
    assessment = card.get('assessment') or {}
    score = assessment.get('score')
    if score is not None and (type(score) is not int or not 0<=score<=100):
        findings.append(_finding(card,'score','SCORE_OUT_OF_RANGE'))
    return findings


def _controls() -> list[dict]:
    """Literal hand-calculated examples detect a shared calculator regression."""
    position = dict(id='control',lot=1,customer=None,name='Control',quantity='2',unit='шт',item_price='5',position_price='10',currency='RUB')
    stock = {'data_kind':'reported','rows':[dict(row=2,sku='control',product='Control',free_quantity='2',unit='шт',unit_cost='3',currency='RUB',as_of=None,attributes={})]}
    matching = MatchingResult({'control':PositionMatches({'control':[]},False)})
    calculated = calculate_analysis({'positions':[position],'limitations':[]},stock,matching)
    controls = [{'id':'stock_profit','expected':'4','actual':calculated['economics']['profit']}]
    for name,plan,expected in [
        ('score_half_target',{'status':'feasible','stock_share':'1','utilization':'0','full_economics':{'scope':'full','currency':'RUB','revenue':'100','cost':'85'}},50),
        ('score_infeasible',{'status':'infeasible'},0),
        ('score_unknown',{'status':'needs_review'},None),
    ]:
        controls.append({'id':name,'expected':expected,'actual':score_assessment({},plan)['score']})
    for control in controls:
        control['passed'] = control['actual'] == control['expected']
    return controls


def _same(left,right) -> bool:
    return json.dumps(left,sort_keys=True,ensure_ascii=False,default=str) == json.dumps(right,sort_keys=True,ensure_ascii=False,default=str)


def _stamp(card: dict) -> datetime | None:
    value = card.get('evaluated_at') or card.get('created_at')
    if isinstance(value,datetime):
        return value if value.utcoffset() is not None else None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.utcoffset() is not None else None
    except (TypeError,ValueError):
        return None


def audit_run(cards: list[dict],stock: dict,profile: dict,model: ModelClient,request_id: str,
              heartbeat: Callable[[],None],reference: dict | None = None,*,max_cards: int | None = None,
              progress: Callable[[dict],None] | None = None) -> dict:
    if max_cards is not None and (type(max_cards) is not int or max_cards<1):
        raise CoreError('VALIDATION_ERROR','Число проверяемых карточек должно быть положительным.')
    if reference is not None and not isinstance(reference,dict):
        raise CoreError('VALIDATION_ERROR','Ручной эталон должен быть словарём по analysis_id.')
    findings,models,checked_ids = [],set(),set()
    controls = _controls()
    for control in controls:
        if not control['passed']:
            findings.append({'analysis_id':None,'stage':'controls','severity':'error','reason':'LITERAL_CONTROL_FAILED',**control})
    comparison = dict(provided=reference is not None,checked=0,agreements=0,disagreements=0,
                      note='Повторное согласие модели не доказывает точность. Ручной эталон сравнивается отдельно.')
    for index,card in enumerate(cards[:max_cards]):
        heartbeat()
        if progress:
            progress({'status':'running','checked':len(checked_ids),'examined':index,'total':len(cards),
                      'findings':[],'unchecked_analysis_ids':[],'limitations':[]})
        if (not isinstance(card.get('tender'),dict) or not isinstance(card.get('analysis'),dict)
                or not isinstance(stock,dict) or not isinstance(stock.get('rows'),list) or not isinstance(profile,dict)):
            findings.append(_finding(card,'snapshot','SNAPSHOT_INPUT_MISSING','warning'))
            continue
        findings.extend(check_invariants(card,stock,profile))
        try:
            if not isinstance(card.get('raw_tender'),dict):
                raise CoreError('RAW_SNAPSHOT_MISSING','Исходный снимок до извлечения документов не сохранён.')
            # Repeat extraction from raw inputs; saved derived facts are never sent back to the model.
            version = (card['tender'].get('document_extraction') or {}).get('prompt_version', LEGACY_PROMPT_VERSION)
            tender = enrich_tender(card['raw_tender'],model,f'{request_id}-extract-{index}',heartbeat,
                                   profile=profile,prompt_version=version)
            extraction = tender.get('document_extraction') or {}
            if extraction.get('model_version'):
                models.add(extraction['model_version'])
            if extraction.get('state')=='unknown':
                error = extraction.get('error') or {}
                raise CoreError(error.get('code','DOCUMENT_EXTRACTION_UNKNOWN'),error.get('message','Извлечение документов не завершено.'))
            for field in ('positions','terms','limitations'):
                if not _same(card['tender'].get(field),tender.get(field)):
                    findings.append(_finding(card,'documents','EXTRACTION_DISAGREEMENT','warning',field=field,
                        persisted=card['tender'].get(field),rechecked=tender.get(field)))
            matching = match_positions(tender,stock,model,f'{request_id}-card-{index}',heartbeat)
            if matching.model:
                models.add(matching.model)
            if matching.error:
                raise CoreError(matching.error['code'],matching.error['message'])
            repeated = calculate_analysis(tender,stock,matching)
            previous = {p['id']:p for p in card['analysis'].get('positions',[])}
            for line in repeated['positions']:
                persisted = sorted(m['sku'] for m in previous.get(line['id'],{}).get('matches',[]))
                current = sorted(m['sku'] for m in line['matches'])
                if persisted != current:
                    findings.append(_finding(card,'matching','MATCHING_DISAGREEMENT','warning',position_id=line['id'],persisted=persisted,rechecked=current,evidence=line['matches']))
            for field in ('coverage_percent','economics','status'):
                if not _same(card['analysis'].get(field),repeated.get(field)):
                    findings.append(_finding(card,'calculation','CALCULATION_DISAGREEMENT','warning',field=field,persisted=card['analysis'].get(field),rechecked=repeated.get(field)))
            complete = check_completeness(tender)
            if card.get('completeness') is not None and not _same(card['completeness'],complete):
                findings.append(_finding(card,'selection','COMPLETENESS_DISAGREEMENT','warning',persisted=card['completeness'],rechecked=complete))
            evaluated_at = _stamp(card)
            if evaluated_at is None:
                findings.append(_finding(card,'production','EVALUATION_TIME_MISSING','warning'))
                continue
            if not isinstance(card.get('fulfillment'),dict) or not isinstance(card.get('assessment'),dict):
                findings.append(_finding(card,'snapshot','SNAPSHOT_STAGE_MISSING','warning'))
                continue
            plan = evaluate_fulfillment(tender,stock,repeated,profile,now=evaluated_at,
                                       rule_version=card['fulfillment'].get('version',1))
            assessment = score_assessment(tender,plan,profile.get('scoring_policy'))
            if not _same(card['fulfillment'],plan):
                findings.append(_finding(card,'production','FULFILLMENT_DISAGREEMENT','warning',persisted=card['fulfillment'],rechecked=plan))
            fields = ('score','feasibility','priority','full_economics')
            if assessment['version'] == 1:
                fields += ('stress_score',)
            for field in fields:
                if not _same(card['assessment'].get(field),assessment.get(field)):
                    findings.append(_finding(card,'score','ASSESSMENT_DISAGREEMENT','warning',field=field,persisted=card['assessment'].get(field),rechecked=assessment.get(field)))
            identifier = str(card.get('analysis_id'))
            if reference is not None and identifier in reference:
                expected = reference[identifier]
                if not isinstance(expected,dict):
                    raise CoreError('VALIDATION_ERROR','Эталон карточки должен быть словарём.')
                actual = {key:assessment.get(key) for key in expected if key!='matching'}
                if 'matching' in expected:
                    actual['matching'] = {line['id']:sorted(match['sku'] for match in line['matches']) for line in repeated['positions']}
                comparison['checked'] += 1
                agrees = _same(actual,expected)
                comparison['agreements' if agrees else 'disagreements'] += 1
                if not agrees:
                    findings.append(_finding(card,'reference','REFERENCE_DISAGREEMENT','warning',expected=expected,rechecked=actual))
            checked_ids.add(identifier)
        except CoreError as error:
            findings.append(_finding(card,'recheck',error.code,'warning',message=error.message))
    unchecked = [str(card.get('analysis_id','unknown')) for card in cards if str(card.get('analysis_id')) not in checked_ids]
    return {'status':'partial' if unchecked or not all(c['passed'] for c in controls) else 'complete',
            'checked':len(checked_ids),'total':len(cards),'findings':findings,'unchecked_analysis_ids':unchecked,
            'reference_comparison':comparison,'rule_versions':RULE_VERSIONS,'rule_controls':controls,
            'model_version':next(iter(models)) if len(models)==1 else None,'model_versions':sorted(models),
            'limitations':['Проверены только сохранённые кандидаты; охват исходного поиска не расширялся.',
                           'Карточки независимы: совместное участие в нескольких тендерах не проверяется.',
                           'Воспроизводимость тем же калькулятором и согласие модели не доказывают точность правил.']}
