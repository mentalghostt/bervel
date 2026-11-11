from copy import deepcopy
from datetime import datetime,timezone

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.calculation import calculate_analysis
from bervel.modules.ai_tender_radar.matching import match_positions
from bervel.modules.ai_tender_radar.production import evaluate_fulfillment
from bervel.modules.ai_tender_radar.scoring import score_assessment
from test_ai_tender_radar_analysis import SelectingModel, tender, need, stock, row


NOW = datetime(2026,11,2,tzinfo=timezone.utc)


def inputs(analysis_id='a1',reject=False,rule_version=2):
    demand = tender(need('Болт М12',quantity=10,Price=10,Amount=100))
    demand['positions'][0].update(item_price='10',position_price='100',currency='RUB')
    demand['terms'] = dict(start_at=NOW.isoformat(),delivery_deadline='2026-11-06T00:00:00+00:00',delivery_days=0,
                           logistics_cost='0',other_cost='0',tax_comparable=True,confirmed_at=NOW.isoformat())
    warehouse = stock(row('B12','Болт М12',free='10',cost='5'))
    warehouse['rows'][0]['as_of'] = NOW.isoformat()
    profile = dict(products=[],production=[],resources=[],scoring_policy={'max_age_by_source':{'stock':'7','costs':'7'}})
    def omit(result):
        for position in result['positions']:
            position['matches'] = []
        return result
    matching = match_positions(demand,warehouse,SelectingModel(omit if reject else lambda r:r),'initial',lambda:None)
    analysis = calculate_analysis(demand,warehouse,matching)
    plan = evaluate_fulfillment(demand,warehouse,analysis,profile,now=NOW,rule_version=rule_version)
    assessment = score_assessment(demand,plan,profile['scoring_policy'])
    return dict(analysis_id=analysis_id,evaluated_at=NOW.isoformat(),tender=demand,raw_tender=deepcopy(demand),analysis=analysis,
                fulfillment=plan,assessment=assessment),warehouse,profile


def test_fresh_matching_can_find_previously_rejected_product_without_old_answers():
    from bervel.modules.ai_tender_radar.audit import audit_run
    card,warehouse,profile = inputs(reject=True)
    before = deepcopy(card)
    model = SelectingModel()
    result = audit_run([card],warehouse,profile,model,'audit',lambda:None)
    matching = [f for f in result['findings'] if f['stage'] == 'matching']
    assert matching[0]['reason'] == 'MATCHING_DISAGREEMENT'
    assert matching[0]['rechecked'] == ['B12']
    assert matching[0]['persisted'] == []
    assert matching[0]['evidence']
    assert result['checked'] == 1
    assert result['status'] == 'complete'
    assert card == before
    assert model.calls
    assert result['reference_comparison']['provided'] is False
    assert 'accuracy' not in result


def test_historical_v1_audit_repeats_the_saved_rules_without_false_disagreement():
    from bervel.modules.ai_tender_radar.audit import audit_run
    card,warehouse,profile=inputs(rule_version=1)
    result=audit_run([card],warehouse,profile,SelectingModel(),'legacy-audit',lambda:None)
    assert result['checked']==1
    assert not any(f['reason']=='FULFILLMENT_DISAGREEMENT' for f in result['findings'])
    assert card['fulfillment']['version']==1


def test_independent_invariants_find_double_stock_and_resource_spending():
    from bervel.modules.ai_tender_radar.audit import check_invariants
    card,warehouse,profile = inputs()
    allocation = card['analysis']['positions'][0]['allocations'][0]
    card['analysis']['positions'][0]['allocations'].append(deepcopy(allocation))
    card['fulfillment']['schedule'] = [dict(position_id='p',resource_group_id='g',date='2026-11-02',workdays='0.7'),
                                        dict(position_id='q',resource_group_id='g',date='2026-11-02',workdays='0.7')]
    profile['resources'] = [dict(group_id='g',calendar=[0],free_capacity_by_day={'2026-11-02':'1'},confirmed_at=NOW.isoformat())]
    reasons = {finding['reason'] for finding in check_invariants(card,warehouse,profile)}
    assert 'STOCK_OVERALLOCATED' in reasons
    assert 'RESOURCE_OVERALLOCATED' in reasons


def test_budget_reports_unchecked_cards_and_literal_controls():
    from bervel.modules.ai_tender_radar.audit import audit_run
    first,warehouse,profile = inputs()
    second = deepcopy(first); second['analysis_id'] = 'a2'
    result = audit_run([first,second],warehouse,profile,SelectingModel(),'audit',lambda:None,max_cards=1)
    assert result['status'] == 'partial'
    assert result['checked'] == 1
    assert result['total'] == 2
    assert result['unchecked_analysis_ids'] == ['a2']
    assert result['rule_controls']
    assert all(control['passed'] for control in result['rule_controls'])
    assert {control['id'] for control in result['rule_controls']} >= {'stock_profit','score_half_target','score_infeasible'}


def test_reference_is_compared_separately_from_model_agreement():
    from bervel.modules.ai_tender_radar.audit import audit_run
    card,warehouse,profile = inputs()
    result = audit_run([card],warehouse,profile,SelectingModel(),'audit',lambda:None,
                       reference={'a1':{'score':0,'feasibility':'infeasible'}})
    assert result['reference_comparison']['provided'] is True
    assert result['reference_comparison']['disagreements'] == 1
    assert any(f['reason'] == 'REFERENCE_DISAGREEMENT' for f in result['findings'])
    assert not any(f['reason'] == 'MATCHING_DISAGREEMENT' for f in result['findings'])


def test_model_failure_and_missing_materials_leave_partial_report_and_other_cards_checked():
    from bervel.modules.ai_tender_radar.audit import audit_run
    first,warehouse,profile = inputs()
    second = deepcopy(first); second['analysis_id'] = 'a2'
    class FailOnce(SelectingModel):
        failed = False
        def generate(self,*args,**kwargs):
            if not self.failed:
                self.failed = True
                raise CoreError('MODEL_TIMEOUT','Timeout')
            return super().generate(*args,**kwargs)
    result = audit_run([first,second,{'analysis_id':'missing'}],warehouse,profile,FailOnce(),'audit',lambda:None)
    assert result['status'] == 'partial'
    assert result['checked'] == 1
    assert result['total'] == 3
    assert set(result['unchecked_analysis_ids']) == {'a1','missing'}
    reasons = {f['reason'] for f in result['findings']}
    assert 'MODEL_TIMEOUT' in reasons
    assert 'SNAPSHOT_INPUT_MISSING' in reasons


def test_no_original_evaluation_time_does_not_use_current_clock():
    from bervel.modules.ai_tender_radar.audit import audit_run
    card,warehouse,profile = inputs()
    del card['evaluated_at']
    result = audit_run([card],warehouse,profile,SelectingModel(),'audit',lambda:None)
    assert result['status'] == 'partial'
    assert any(f['reason'] == 'EVALUATION_TIME_MISSING' for f in result['findings'])


def test_audit_reextracts_raw_documents_without_saved_enrichment(monkeypatch):
    from bervel.modules.ai_tender_radar.audit import audit_run
    card,warehouse,profile = inputs()
    original = deepcopy(card)
    seen = []
    def extract(raw,model,request_id,heartbeat,*,profile,prompt_version):
        seen.append(deepcopy(raw))
        result = deepcopy(raw)
        result['terms']['delivery_deadline'] = '2026-11-05T00:00:00+00:00'
        result['document_extraction'] = {'state':'complete','model_version':'test-extractor'}
        return result
    monkeypatch.setattr('bervel.modules.ai_tender_radar.audit.enrich_tender',extract)
    result = audit_run([card],warehouse,profile,SelectingModel(),'audit',lambda:None)
    assert seen == [original['raw_tender']]
    assert any(f['reason']=='EXTRACTION_DISAGREEMENT' and f['field']=='terms' for f in result['findings'])
    assert 'test-extractor' in result['model_versions']
    assert card == original


def test_missing_raw_snapshot_is_partial_even_if_derived_data_is_present():
    from bervel.modules.ai_tender_radar.audit import audit_run
    card,warehouse,profile = inputs()
    del card['raw_tender']
    result = audit_run([card],warehouse,profile,SelectingModel(),'audit',lambda:None)
    assert result['status']=='partial'
    assert result['checked']==0
    assert any(f['reason']=='RAW_SNAPSHOT_MISSING' for f in result['findings'])


def test_unknown_fresh_extraction_is_unchecked(monkeypatch):
    from bervel.modules.ai_tender_radar.audit import audit_run
    card,warehouse,profile = inputs()
    def extract(raw,*args,**kwargs):
        result = deepcopy(raw)
        result['document_extraction']={'state':'unknown','error':{'code':'MODEL_TIMEOUT','message':'Timeout'}}
        return result
    monkeypatch.setattr('bervel.modules.ai_tender_radar.audit.enrich_tender',extract)
    result = audit_run([card],warehouse,profile,SelectingModel(),'audit',lambda:None)
    assert result['status']=='partial'
    assert result['checked']==0
    assert any(f['reason']=='MODEL_TIMEOUT' for f in result['findings'])
