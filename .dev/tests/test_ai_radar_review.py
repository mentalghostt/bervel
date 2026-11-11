import hashlib
import json
from copy import deepcopy

import pytest

from bervel.core.errors import CoreError


def card():
    return {'analysis_id':'a1','tender':{'positions':[{'id':'p1','quantity':'10','name':'Болт','unit':'шт'}]},
            'analysis':{'positions':[]},'fulfillment':{'status':'feasible','stock_allocations':[{'position_id':'p1','sku':'S1','stock_quantity':'10'}],
            'production_allocations':[],'schedule':[],'full_economics':{'profit':'50','scope':'full'}},
            'assessment':{'score':80,'full_economics':{'profit':'50','scope':'full'}},'completeness':{'complete':True}}


class ReviewModel:
    def __init__(self,edit=None,error=None):
        self.edit,self.error,self.calls = edit,error,[]

    def generate(self,request_id,prompt,*,output_schema,web_search=False,timeout_seconds=None):
        self.calls.append((request_id,prompt))
        assert web_search is False
        assert output_schema['additionalProperties'] is False
        payload = json.loads(prompt.split('\nВХОД\n',1)[1])
        assert set(payload) <= {'card','question','calculated'}
        if self.error:
            raise CoreError(self.error,'Model failed')
        answer = dict(recommendation='Склад покрывает заказ. Прибыль 50, балл 80.',risks=[],assumptions=[],
                      evidence_refs=['position:p1','assessment'],calculated=deepcopy(payload['calculated']),
                      decision='participate',proposes_production=False)
        if 'question' in payload:
            answer['answer'] = 'Подтверждена прибыль 50.'
        if self.edit:
            self.edit(answer)
        return {'answer':json.dumps(answer,ensure_ascii=False),'model':'fixture-review'}


def test_review_is_bound_to_calculation_and_does_not_mutate_card():
    from bervel.modules.ai_tender_radar.review import review_card
    source = card()
    before = deepcopy(source)
    result = review_card(source,ReviewModel(),'request',lambda:None)
    assert result['state'] == 'complete'
    assert result['evidence_refs'] == ['position:p1','assessment']
    assert result['model_version'] == 'fixture-review'
    assert result['prompt_version'] == 'card-review-v1'
    assert any('тендер' in text and 'резерв' in text for text in result['assumptions'])
    assert source == before


@pytest.mark.parametrize(('status', 'decision', 'checks', 'success'), [
    ('feasible', 'participate', [], True),
    ('feasible', 'participate', ['Уточнить стоимость доставки.'], False),
    ('needs_review', 'review', ['Уточнить стоимость доставки.'], True),
    ('needs_review', 'review', [], False),
    ('needs_review', 'review', ['Перепроверить весь тендер.'], False),
    ('needs_review', 'review', ['Перепроверить всё в тендере.'], False),
    ('infeasible', 'review', ['Уточнить стоимость доставки.'], False),
    ('infeasible', 'decline', [], True),
])
def test_compact_review_only_requests_material_manual_checks(status, decision, checks, success):
    from bervel.modules.ai_tender_radar.review import review_card
    source = card()
    source['fulfillment'].update(version=4, status=status)
    if status == 'needs_review':
        source['fulfillment']['reasons'] = ['LOGISTICS_COST_UNKNOWN']
        source['assessment'].update(score=None, full_economics={'scope': None})
        source['fulfillment']['full_economics'] = {'scope': None}

    def compact(answer):
        answer.pop('risks')
        answer.pop('assumptions')
        reason = {'participate': 'Заказ выполним с прибылью.',
                  'review': 'Без стоимости доставки нельзя оценить выгоду.',
                  'decline': 'Заказ не получится выполнить в срок.'}[decision]
        answer.update(decision=decision, recommendation=reason, manual_checks=checks)

    result = review_card(source, ReviewModel(compact), 'compact', lambda: None, prompt_version='card-review-v4')
    assert result['state'] == ('complete' if success else 'failed')
    if success:
        assert result['decision'] == decision
        assert result['manual_checks'] == checks
    else:
        assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


@pytest.mark.parametrize('reason', ['Совпадение unconfirmed.', 'Складская маржа 80.327180301%.'])
def test_compact_review_rejects_technical_status_and_excessive_precision(reason):
    from bervel.modules.ai_tender_radar.review import review_card
    source = card()
    source['fulfillment']['version'] = 4
    source['analysis']['economics'] = {'scope': 'confirmed', 'margin_percent': '80.327180301'}

    def compact(answer):
        answer.pop('risks')
        answer.pop('assumptions')
        answer.update(recommendation=reason, manual_checks=[])

    result = review_card(source, ReviewModel(compact), 'compact', lambda: None, prompt_version='card-review-v4')
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_current_review_prompt_uses_one_plan_and_rejects_fabricated_stress_score():
    from bervel.modules.ai_tender_radar.review import review_card
    source = card()
    source['assessment']['version'] = 2
    model = ReviewModel()
    result = review_card(source, model, 'current', lambda: None)
    assert result['state'] == 'complete'
    assert result['prompt_version'] == 'card-review-v2'
    assert 'стресс' not in model.calls[0][1].split('\nВХОД\n', 1)[0]

    invented = ReviewModel(lambda answer: answer.update(recommendation='В стресс-сценарии балл 80.'))
    result = review_card(source, invented, 'invented', lambda: None)
    assert result['state'] == 'failed'
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_new_review_checks_reported_margin_and_coverage_without_generic_appendix():
    from bervel.modules.ai_tender_radar.review import review_card
    source = card()
    source['fulfillment']['version'] = 4
    source['assessment']['version'] = 2
    source['analysis']['coverage_percent'] = '100'
    source['assessment']['full_economics'].update(scope='full', margin_percent='25')
    source['fulfillment']['full_economics']['margin_percent'] = '25'
    model = ReviewModel(lambda answer: answer.update(
        recommendation='Склад покрывает 100%. Прибыль 50, маржа 25%, балл 80.'))

    result = review_card(source, model, 'current', lambda: None)
    assert result['state'] == 'complete'
    assert result['prompt_version'] == 'card-review-v3'
    assert result['assumptions'] == []
    prompt, payload = model.calls[0][1].split('\nВХОД\n', 1)
    assert 'логистик' in prompt and 'марж' in prompt and 'покрыти' in prompt
    facts = json.loads(payload)['calculated']
    assert facts['margin_percent'] == '25' and facts['coverage_percent'] == '100'

    for claimed in ('Маржа 50%.', 'Покрытие склада 25%.'):
        fabricated = ReviewModel(lambda answer: answer.update(recommendation=claimed))
        result = review_card(source, fabricated, 'invented', lambda: None)
        assert result['state'] == 'failed'
        assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_current_review_can_explain_a_known_stock_loss_when_full_profit_is_unknown():
    from bervel.modules.ai_tender_radar.review import review_card
    source = card()
    source['fulfillment'].update(version=4, status='needs_review', full_economics={'scope': None})
    source['assessment'].update(version=2, score=None, full_economics={'scope': None})
    source['analysis'].update(coverage_percent='100',
        economics={'scope': 'full', 'profit': '-50', 'margin_percent': '-25'})
    model = ReviewModel(lambda answer: answer.update(decision='review',
        recommendation='Складская прибыль -50, складская маржа -25%. Для полного расчёта нужна цена доставки.'))
    result = review_card(source, model, 'loss', lambda: None)
    assert result['state'] == 'complete'

    unsupported = ReviewModel(lambda answer: answer.update(decision='review',
        recommendation='Прибыль -50, маржа -25%.'))
    assert review_card(source, unsupported, 'unscoped', lambda: None)['state'] == 'failed'


@pytest.mark.parametrize('edit',[
    lambda a:a['calculated'].update(score=99),
    lambda a:a['calculated'].update(profit='500'),
    lambda a:a['calculated'].update(plan_fingerprint='changed'),
    lambda a:a.update(evidence_refs=['position:invented']),
    lambda a:a.update(recommendation='Ожидается прибыль 999999.'),
    lambda a:a.update(proposes_production=True),
])
def test_unverifiable_model_claims_fail_without_publishing_confident_advice(edit):
    from bervel.modules.ai_tender_radar.review import review_card
    result = review_card(card(),ReviewModel(edit),'request',lambda:None)
    assert result['state'] == 'failed'
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'
    assert result['recommendation'] == ''


def test_review_timeout_preserves_calculated_card_and_never_retries():
    from bervel.modules.ai_tender_radar.review import review_card
    source = card()
    model = ReviewModel(error='MODEL_TIMEOUT')
    result = review_card(source,model,'request',lambda:None)
    assert result['state'] == 'failed'
    assert result['error']['code'] == 'MODEL_TIMEOUT'
    assert source['assessment']['score'] == 80
    assert len(model.calls) == 1


def test_question_uses_only_historical_card_and_validates_question():
    from bervel.modules.ai_tender_radar.review import question_card
    model = ReviewModel()
    result = question_card(card(),'Какова прибыль?',model,'q1',lambda:None)
    assert result['state'] == 'complete'
    assert result['answer'] == 'Подтверждена прибыль 50.'
    assert 'Какова прибыль?' in model.calls[0][1]
    for question in ('',' '*10,'x'*2001):
        with pytest.raises(CoreError) as error:
            question_card(card(),question,model,'q2',lambda:None)
        assert error.value.code == 'VALIDATION_ERROR'
    assert len(model.calls) == 1


def test_infeasible_or_incomplete_card_cannot_receive_positive_decision():
    from bervel.modules.ai_tender_radar.review import review_card
    for key in ('fulfillment','completeness'):
        data = card()
        if key == 'fulfillment':
            data[key]['status'] = 'infeasible'
        else:
            data[key]['complete'] = False
        result = review_card(data,ReviewModel(),'r',lambda:None)
        assert result['state'] == 'failed'


@pytest.mark.parametrize('text', ['Балл 50, прибыль 80.', 'Ожидаемая прибыль: 80 RUB.'])
def test_existing_numbers_cannot_be_relabelled_as_score_or_profit(text):
    from bervel.modules.ai_tender_radar.review import review_card
    result = review_card(card(),ReviewModel(lambda a:a.update(recommendation=text)),'r',lambda:None)
    assert result['state'] == 'failed'
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_context_limit_does_not_silently_truncate_the_saved_card():
    from bervel.modules.ai_tender_radar.review import review_card
    source = card()
    source['tender']['description'] = 'x'*(200*1024)
    model = ReviewModel()
    result = review_card(source,model,'r',lambda:None)
    assert result['state'] == 'failed'
    assert result['error']['code'] == 'MODEL_CONTEXT_LIMIT'
    assert model.calls == []


@pytest.mark.parametrize('text,expected',[
    ('Балл 80, прибыль 50. В стресс-сценарии прибыль — 35 RUB, балл 70.', 'complete'),
    ('Базовая прибыль 50, стресс-прибыль 35. Стресс-балл 70.', 'complete'),
    ('Базовая прибыль 35. Стресс-балл 70.', 'failed'),
    ('Балл 80. В стресс-сценарии прибыль 50.', 'failed'),
    ('В стресс-сценарии основные риски сохраняются, прибыль 50.', 'failed'),
    ('Прибыль в стресс-сценарии: 50.', 'failed'),
    ('Прибыль 35 в стресс-сценарии.', 'complete'),
    ('Прибыль в стресс-сценарии: 35 RUB.', 'complete'),
])
def test_baseline_and_stress_numbers_keep_their_explicit_scope(text,expected):
    from bervel.modules.ai_tender_radar.review import review_card
    source=card()
    source['fulfillment']['stress']={'status':'feasible','full_economics':{'profit':'35','scope':'full'}}
    source['assessment']['stress_score']=70
    result=review_card(source,ReviewModel(lambda a:a.update(recommendation=text)),'r',lambda:None)
    assert result['state']==expected
