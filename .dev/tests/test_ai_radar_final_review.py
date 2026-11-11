from copy import deepcopy
from test_ai_radar_production import case, evaluate


def test_unknown_document_requirements_prevent_confident_profitability():
    from bervel.modules.ai_tender_radar.scoring import score_assessment
    data = case(covered='100')
    data[0]['documents'] = [{'text':'Обязательное покрытие: #####'}]
    data[0]['source_card'] = {'Docs':[{}]}
    plan = evaluate(data)
    assert plan['status'] == 'needs_review'
    assert score_assessment(data[0], plan)['score'] is None


def test_audit_accepts_valid_fractional_calendar_certificate():
    from bervel.modules.ai_tender_radar.audit import check_invariants
    data = case(covered='0', required='100', rate='30', days=7)
    plan = evaluate(data)
    assert plan['status'] == 'feasible'
    card = {'analysis_id':'a', 'tender':data[0], 'analysis':data[2], 'fulfillment':plan, 'assessment':{}}
    findings = check_invariants(card, data[1], data[3])
    assert not [f for f in findings if f['reason'] in {'SCHEDULE_ALLOCATION_INVALID','SCHEDULE_WORK_CONFLICT'}]


def test_shared_stock_reallocation_prevents_false_never_manufactured_rejection():
    data = case(covered='100')
    tender, stock, analysis, profile = data
    second = deepcopy(tender['positions'][0]); second['id'] = 'q'; tender['positions'].append(second)
    analysis['positions'][0]['matches'].append({'sku':'t','evidence':['verified']})
    q = deepcopy(analysis['positions'][0]); q.update(id='q',covered='0',shortage='100',allocations=[],matches=[{'sku':'s','evidence':['verified']}])
    analysis['positions'].append(q)
    profile['products'][0]['manufactured_before'] = False
    alternate = deepcopy(profile['products'][0]); alternate.update(id='alternate',sku='t',manufactured_before=True)
    profile['products'].append(alternate)
    profile['production'][0].update(product_id='alternate',units_per_workday='100')
    plan = evaluate(data)
    assert plan['status'] == 'needs_review'
    assert 'ALTERNATIVE_STOCK_ALLOCATION_UNRESOLVED:q' in plan['reasons']
