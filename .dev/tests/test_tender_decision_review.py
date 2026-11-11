"""Read-only analytical research around the saved tender calculation."""
from copy import deepcopy
import json

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar import review
from test_ai_radar_review import card


def source_card():
    value = card()
    value['tender']['id'] = 't1'
    value['fulfillment'].update(version=4, status='needs_review', full_economics={'scope': None})
    value['assessment'].update(score=None, full_economics={'scope': None})
    value['opportunity'] = {'score': 1, 'basis': 'confirmed', 'known_share': '0.0027', 'margin_percent': '25'}
    value['analysis'].update(economics={'scope': 'confirmed', 'profit': '50', 'margin_percent': '25'},
        positions=[{'id': 'p1', 'required': '25680', 'covered': '69', 'shortage': '25611'}])
    return value


def final(payload, *, answer=None, refs=None, calculations=None):
    result = dict(recommendation='На складе есть подтверждённый объём. Для остальной поставки нужен план.\n\n'
        'Складская прибыль 50. Для оценки полного заказа нужна стоимость доставки.',
        risks=[], assumptions=[], evidence_refs=refs or ['assessment'],
        calculated=deepcopy(payload['calculated']), decision='review', proposes_production=False)
    if 'question' in payload:
        result.update(answer=answer or 'Для полного расчёта нужна стоимость доставки.', calculations=calculations or [])
    return {'action': 'final', 'ref': '', 'offset': 0, 'length': 12000, 'query': '', 'notes': '', 'response': result}


def action(name, ref='', **kwargs):
    return {'action': name, 'ref': ref, 'offset': 0, 'length': 12000, 'query': '', 'notes': '', 'response': None, **kwargs}


class ResearchModel:
    def __init__(self, steps):
        self.steps, self.calls = steps, []

    def generate(self, request_id, prompt, *, output_schema, web_search, timeout_seconds):
        assert web_search is False
        assert len(prompt.encode()) <= review.MAX_CONTEXT_BYTES
        payload = json.loads(prompt.split('\nВХОД\n', 1)[1])
        self.calls.append(payload)
        # A terminal invalid fixture remains invalid on the one allowed repair;
        # explicit multi-step fixtures script a corrected response when needed.
        step = self.steps[min(len(self.calls)-1, len(self.steps)-1)]
        if isinstance(step, Exception):
            raise step
        answer = step(payload) if callable(step) else step
        return {'answer': json.dumps(answer, ensure_ascii=False), 'model': 'research-fixture'}


def test_v5_report_uses_opportunity_without_mutating_snapshot():
    source = source_card()
    before = deepcopy(source)
    model = ResearchModel([final])
    result = review.review_card(source, model, 'r', lambda: None, prompt_version='card-review-v5', stock={}, profile={})
    assert result['state'] == 'complete'
    assert result['prompt_version'] == 'card-review-v5'
    assert result['decision'] == 'review'
    assert model.calls[0]['calculated']['score'] == 1
    assert source == before


def test_big_document_profile_read_search_and_conversation_history():
    source = source_card()
    source['tender']['documents'] = [{'filename': 'terms.txt', 'text': 'начало ' + 'x'*220000 + ' Срок 42 дня.'}]
    profile = {'products': [{'sku': 'S1', 'detail': 'x'*5_000_000}], 'lead_time': '42'}
    history = [{'question': 'Какой документ?', 'answer': 'Нужен срок из условий.'}]
    originals = deepcopy((source, profile, history))

    def after_search(payload):
        assert 'Срок 42 дня' in json.dumps(payload['tool_result'], ensure_ascii=False)
        return action('read', 'document:0', offset=220000)

    def after_read(payload):
        assert 'Срок 42 дня' in payload['tool_result']['content']
        return action('read', 'history:')

    def after_history(payload):
        assert 'Какой документ?' in payload['tool_result']['content']
        return final(payload, answer='В документе срок 42 дня.', refs=['document:0'])

    model = ResearchModel([action('search', 'document:0', query='Срок'), after_search, after_read, after_history])
    result = review.question_card(source, 'А какой там срок?', model, 'q', lambda: None,
                                  profile=profile, stock={}, history=history)
    assert result['state'] == 'complete', result
    assert len(model.calls) == 4
    assert (source, profile, history) == originals


@pytest.mark.parametrize('bad_ref', ['document:99', 'card:/tender/missing', 'https://evil.test', 'other-tender:'])
def test_invalid_references_fail_explicitly(bad_ref):
    model = ResearchModel([action('read', bad_ref)])
    result = review.question_card(source_card(), 'Прочитай условия', model, 'q', lambda: None, history=[])
    assert result['state'] == 'failed'
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'
    assert len(model.calls) == 1


def test_refresh_is_current_tender_only_and_does_not_overwrite_calculation():
    source = source_card()
    original = deepcopy(source)
    calls = []

    def fetch():
        calls.append(True)
        return {'id': 't1', 'documents': [{'text': 'Актуальный срок 21 день.'}]}

    model = ResearchModel([action('refresh'), action('read', 'refreshed:/documents/0/text'),
        lambda payload: final(payload, answer='В обновлённом источнике срок 21 день.', refs=['refreshed:/documents/0/text'])])
    result = review.question_card(source, 'Обнови источник и проверь срок.', model, 'q', lambda: None,
                                  fetch_source=fetch)
    assert result['state'] == 'complete', result
    assert calls == [True]
    assert source == original


def test_refresh_does_not_accept_a_different_tender():
    model = ResearchModel([action('refresh')])
    result = review.question_card(source_card(), 'Обнови источник.', model, 'q', lambda: None,
                                  fetch_source=lambda: {'id': 'other'})
    assert result['state'] == 'failed'
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_chat_verifies_hypothetical_arithmetic_and_requires_assumptions():
    calculation = {'operation': 'subtract', 'operands': ['25680', '69'], 'result': '25611',
                   'assumption': 'При неизменном требуемом и складском объёме.'}
    model = ResearchModel([lambda payload: final(payload, answer='Расчёт при неизменном требуемом и складском объёме: 25680 − 69 = 25611.',
        calculations=[calculation])])
    result = review.question_card(source_card(), 'Какой дефицит?', model, 'q', lambda: None, history=[])
    assert result['state'] == 'complete', result
    calculation['result'] = '1'
    model = ResearchModel([lambda payload: final(payload, answer='Расчёт даёт 1.', calculations=[calculation])])
    assert review.question_card(source_card(), 'Какой дефицит?', model, 'q', lambda: None, history=[])['state'] == 'failed'


def test_timeout_after_read_is_reported_without_retry():
    model = ResearchModel([action('read', 'card:/analysis'), CoreError('MODEL_TIMEOUT', 'Unknown result')])
    result = review.question_card(source_card(), 'Поясни расчёт.', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == 'MODEL_TIMEOUT'
    assert len(model.calls) == 2


def test_fabricated_final_evidence_is_rejected():
    model = ResearchModel([lambda payload: final(payload, refs=['document:fake'])])
    result = review.review_card(source_card(), model, 'r', lambda: None, prompt_version='card-review-v5')
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_projection_facts_prefer_refreshed_economics_to_historical_assessment():
    source = source_card()
    source['assessment']['full_economics'] = {'profit': '999', 'scope': 'full'}
    source['fulfillment']['full_economics'] = {'profit': '123', 'scope': 'full'}
    model = ResearchModel([final])
    result = review.review_card(source, model, 'r', lambda: None, prompt_version='card-review-v5')
    assert result['state'] == 'complete'
    assert model.calls[0]['calculated']['profit'] == '123'


def test_every_document_is_discoverable_and_readable_including_after_preview():
    from bervel.modules.ai_tender_radar.dossier import TenderDossier
    source = source_card()
    source['tender']['documents'] = [{'filename': f'{index}.txt', 'text': f'Условия {index}'} for index in range(40)]
    dossier = TenderDossier(source, {}, {}, [])
    catalog = dossier.catalog()['documents:']
    assert (len(catalog) if isinstance(catalog, list) else catalog['count']) == 40
    index = json.loads(dossier.read('documents:', 0, 16000)['content'])
    assert index[-1]['ref'] == 'document:39'
    assert dossier.read('document:39', 0, 16000)['content'] == 'Условия 39'


def test_entire_large_document_can_be_read_without_context_or_step_cutoff():
    from bervel.modules.ai_tender_radar.dossier import TenderDossier, PAGE_CHARS
    source = source_card()
    document = 'Большой документ. '*50000
    source['tender']['documents'] = [{'text': document}]
    dossier = TenderDossier(source, {}, {}, [])
    collected, offset, reads = '', 0, 0
    while offset is not None:
        portion = dossier.read('document:0', offset, PAGE_CHARS)
        collected += portion['content']
        offset = portion['next_offset']
        reads += 1
    assert collected == document
    assert reads > 24


def test_chat_accepts_new_derived_number_but_rejects_unlabelled_scenario():
    source = source_card()
    calculation = {'operation': 'multiply', 'operands': ['69', '2'], 'result': '138', 'assumption': 'Если склад удвоится.'}
    model = ResearchModel([lambda payload: final(payload, answer='Если склад удвоится, получится 138 штук.', calculations=[calculation])])
    result = review.question_card(source, 'А если склад станет в 2 раза больше?', model, 'q', lambda: None, history=[])
    assert result['state'] == 'complete', result
    model = ResearchModel([lambda payload: final(payload, answer='Склад содержит 138 штук.', calculations=[calculation])])
    result = review.question_card(source, 'А если склад станет в 2 раза больше?', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_v5_report_can_round_long_stock_margin_without_inventing_value():
    source = source_card()
    source['analysis']['economics']['margin_percent'] = '80.327180301'

    def rounded(payload):
        answer = final(payload)
        answer['response']['recommendation'] = 'Складская маржа 80.33%.\n\nДля полного расчёта нужна доставка.'
        return answer

    result = review.review_card(source, ResearchModel([rounded]), 'r', lambda: None, prompt_version='card-review-v5')
    assert result['state'] == 'complete', result


def test_refresh_cannot_be_requested_twice_or_for_an_unrelated_profit_question():
    fetched = []

    def fetch():
        fetched.append(True)
        return {'id': 't1'}

    result = review.question_card(source_card(), 'Какова прибыль?', ResearchModel([action('refresh')]),
                                  'q', lambda: None, fetch_source=fetch)
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'
    assert fetched == []
    result = review.question_card(source_card(), 'Обнови источник.', ResearchModel([action('refresh'), action('refresh')]),
                                  'q', lambda: None, fetch_source=fetch)
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'
    assert fetched == [True]


def test_research_budget_is_finite_and_reports_unfinished_research():
    model = ResearchModel([action('read', 'card:/analysis')] * review.MAX_RESEARCH_STEPS)
    result = review.question_card(source_card(), 'Поясни расчёт.', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == 'MODEL_RESEARCH_LIMIT'
    assert result['answer'] == ''
    assert len(model.calls) == review.MAX_RESEARCH_STEPS


def test_history_preview_includes_recent_turns_without_losing_old_turns():
    from bervel.modules.ai_tender_radar.dossier import TenderDossier
    history = [{'question': f'Вопрос {index}', 'answer': f'Ответ {index}'} for index in range(30)]
    dossier = TenderDossier(source_card(), {}, {}, history)
    assert dossier.catalog()['history:']['recent']['29']['question'] == 'Вопрос 29'
    assert 'Вопрос 0' in dossier.read('history:/0', 0, 16000)['content']


def test_report_accepts_russian_thousands_separators():
    def grouped(payload):
        result = final(payload)
        result['response']['recommendation'] = 'Не хватает 25 611 штук.\n\nНужно проверить производство и стоимость доставки.'
        return result

    result = review.review_card(source_card(), ResearchModel([grouped]), 'r', lambda: None, prompt_version='card-review-v5')
    assert result['state'] == 'complete', result
    assert '25 611' in result['recommendation']


def test_assumptions_must_be_visible_in_the_chat_answer():
    calculation = {'operation': 'multiply', 'operands': ['69', '2'], 'result': '138', 'assumption': 'Если склад удвоится.'}
    model = ResearchModel([lambda payload: final(payload, answer='По расчёту доступно 138 штук.', calculations=[calculation])])
    result = review.question_card(source_card(), 'Если склад станет в 2 раза больше?', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_refresh_cannot_switch_between_simulated_and_real_source():
    source = source_card()
    source['tender']['simulated'] = True
    result = review.question_card(source, 'Обнови источник.', ResearchModel([action('refresh')]),
                                  'q', lambda: None, fetch_source=lambda: {'id': 't1', 'simulated': False})
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_report_requires_coherent_paragraphs_instead_of_one_line():
    def one_line(payload):
        result = final(payload)
        result['response']['recommendation'] = 'Нужно проверить доставку.'
        return result

    result = review.review_card(source_card(), ResearchModel([one_line]), 'r', lambda: None, prompt_version='card-review-v5')
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


@pytest.mark.parametrize('answer', ['Срок поставки 29.12.2026.', 'Срок поставки 29 декабря 2026 года.'])
def test_iso_source_date_accepts_russian_date_rendering(answer):
    source = source_card()
    source['tender']['terms'] = {'delivery_deadline': '2026-12-29T00:00:00+00:00'}
    model = ResearchModel([lambda payload: final(payload, answer=answer)])
    result = review.question_card(source, 'Какой срок?', model, 'q', lambda: None, history=[])
    assert result['state'] == 'complete', result


def test_same_date_components_cannot_be_relabelled_as_a_different_date():
    source = source_card()
    source['tender']['terms'] = {'delivery_deadline': '2026-12-11T00:00:00+00:00'}
    model = ResearchModel([lambda payload: final(payload, answer='Срок поставки 12.11.2026.')])
    result = review.question_card(source, 'Какой срок?', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_stock_profit_accepts_explicit_stock_part_phrase():
    model = ResearchModel([lambda payload: final(payload, answer='В подтверждённой складской части прибыль 50.')])
    result = review.question_card(source_card(), 'Какова прибыль?', model, 'q', lambda: None, history=[])
    assert result['state'] == 'complete', result


def test_previous_computed_result_can_be_quoted_with_history_reference():
    history = [{'question': 'Если 9 умножить на 1.5?', 'answer': 'При допущении увеличения цены расчёт даёт 13.5.'}]
    model = ResearchModel([lambda payload: final(payload, answer='В прошлом сценарии результат 13.5.', refs=['history:/0/answer'])])
    result = review.question_card(source_card(), 'Повтори прошлый результат.', model, 'q', lambda: None, history=history)
    assert result['state'] == 'complete', result


def test_v5_prose_does_not_expose_internal_status_names():
    model = ResearchModel([lambda payload: final(payload, answer='Статус needs_review, opportunity 1.')])
    result = review.question_card(source_card(), 'Каков статус?', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_followup_calculation_can_use_verified_history_chain_without_changing_snapshot():
    source = source_card()
    history = [{'question': 'Если 9 умножить на 1.5?', 'answer': 'При сценарном увеличении цены результат 13.5.',
                'calculations': [{'operation': 'multiply', 'operands': ['9', '1.5'], 'result': '13.5',
                                  'assumption': 'При сценарном увеличении цены.'}]}]
    before = deepcopy((source, history))
    calculation = {'operation': 'multiply', 'operands': ['13.5', '2'], 'result': '27',
                   'assumption': 'Если удвоить прошлый сценарный результат.'}
    model = ResearchModel([lambda payload: final(payload,
        answer='Если удвоить прошлый сценарный результат, расчёт даёт 27.',
        refs=['history:/0/calculations'], calculations=[calculation])])
    result = review.question_card(source, 'Что получится при увеличении прошлого результата в 2 раза?',
                                  model, 'q', lambda: None, history=history)
    assert result['state'] == 'complete', result
    assert result['calculations'] == [calculation]
    assert (source, history) == before


def test_read_memory_keeps_previous_source_when_model_reads_another_source():
    def answer_from_memory(payload):
        refs = [item['request']['ref'] for item in payload['observations']]
        assert 'card:/analysis' in refs
        assert payload['tool_result']['ref'] == 'profile:/production'
        assert len(payload['observations']) <= 3
        assert any(item['ref'] == 'card:/analysis' for item in payload['read_log'])
        return final(payload)

    model = ResearchModel([action('read', 'card:/analysis'), action('read', 'profile:/production'), answer_from_memory])
    result = review.question_card(source_card(), 'Объясни данные.', model, 'q', lambda: None,
                                  profile={'production': [{'units_per_workday': '400'}]}, history=[])
    assert result['state'] == 'complete', result


def test_initial_summary_includes_source_brief_application_deadline():
    source = source_card()
    source['brief'] = {'ApplicationDeadline': '2026-11-22T00:00:00+00:00', 'Customer': 'Заказчик'}

    def answer(payload):
        assert payload['card']['brief']['ApplicationDeadline'] == '2026-11-22T00:00:00+00:00'
        return final(payload, answer='Подать заявку нужно до 22.11.2026.')

    result = review.question_card(source, 'Какой срок заявки?', ResearchModel([answer]), 'q', lambda: None, history=[])
    assert result['state'] == 'complete', result


def test_known_application_deadline_is_not_reported_missing():
    source = source_card()
    source['brief'] = {'ApplicationDeadline': '2026-11-22T00:00:00+00:00'}
    model = ResearchModel([lambda payload: final(payload, answer='Срок подачи заявки неизвестен.')])
    result = review.question_card(source, 'Какой срок заявки?', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_internal_history_reference_belongs_in_evidence_only():
    model = ResearchModel([lambda payload: final(payload, answer='Как ранее (history:/0/answer), нужна доставка.')])
    result = review.question_card(source_card(), 'Повтори вывод.', model, 'q', lambda: None, history=[{'answer': 'Нужна доставка.'}])
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'


def test_read_cache_evicts_old_slices_before_exceeding_context_budget():
    source = source_card()
    source['tender']['documents'] = [{'text': '🛠'*16000} for _ in range(4)]

    def finish(payload):
        assert len(payload['observations']) < 3
        assert len(payload['read_log']) == 4
        assert payload['tool_result']['ref'] == 'document:3'
        return final(payload)

    model = ResearchModel([*[action('read', f'document:{index}', length=16000) for index in range(4)], finish])
    result = review.question_card(source, 'Проверь документы.', model, 'q', lambda: None, history=[])
    assert result['state'] == 'complete', result


def wrong_calculated(payload):
    result = final(payload)
    result['response']['calculated']['score'] = 99
    return result


def test_rejected_final_gets_one_specific_feedback_and_can_be_corrected():
    def corrected(payload):
        feedback = payload['validation_feedback']
        assert feedback['code'] == 'MODEL_INVALID_OUTPUT'
        assert 'calculated' in feedback['message'] or 'рассчитанные' in feedback['message']
        assert feedback['rejected_response']['calculated']['score'] == 99
        assert feedback['details']['calculated_mismatches']['score'] == {'expected': 1, 'received': 99}
        assert feedback['rejected_response']['calculations'] == []
        assert feedback['remaining_corrections'] == 0
        assert payload['calculated']['score'] == 1
        return final(payload)

    source = source_card()
    before = deepcopy(source)
    model = ResearchModel([wrong_calculated, corrected])
    result = review.question_card(source, 'Объясни расчёт.', model, 'q', lambda: None, history=[])
    assert result['state'] == 'complete', result
    assert len(model.calls) == 2
    assert source == before


def test_repeated_final_validation_error_stops_after_single_correction():
    model = ResearchModel([wrong_calculated, wrong_calculated, final])
    result = review.question_card(source_card(), 'Объясни расчёт.', model, 'q', lambda: None, history=[])
    assert result['state'] == 'failed'
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'
    assert result['answer'] == ''
    assert len(model.calls) == 2


@pytest.mark.parametrize('code', ['MODEL_TIMEOUT', 'MODEL_UNAVAILABLE', 'MODEL_BUSY', 'MODEL_RATE_LIMITED', 'MODEL_INVALID_OUTPUT'])
def test_provider_failure_never_triggers_final_correction(code):
    model = ResearchModel([CoreError(code, 'Provider failure'), final])
    result = review.question_card(source_card(), 'Объясни расчёт.', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == code
    assert len(model.calls) == 1


def test_unknown_outcome_during_correction_is_never_retried():
    model = ResearchModel([wrong_calculated, CoreError('MODEL_TIMEOUT', 'Unknown result'), final])
    result = review.question_card(source_card(), 'Объясни расчёт.', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == 'MODEL_TIMEOUT'
    assert len(model.calls) == 2


def test_final_correction_does_not_extend_research_budget():
    model = ResearchModel([*[action('read', 'card:/analysis')] * (review.MAX_RESEARCH_STEPS - 1), wrong_calculated, final])
    result = review.question_card(source_card(), 'Объясни расчёт.', model, 'q', lambda: None, history=[])
    assert result['error']['code'] == 'MODEL_INVALID_OUTPUT'
    assert len(model.calls) == review.MAX_RESEARCH_STEPS


def test_profit_scope_feedback_names_the_claim_without_weakening_validation():
    def corrected(payload):
        details = payload['validation_feedback']['details']
        assert details['claim'] == 'Прибыль 50'
        assert details['calculated_field'] == 'profit'
        assert details['expected'] is None and details['stock_profit'] == '50'
        return final(payload, answer='Складская прибыль 50; прибыль всего заказа неизвестна.')

    model = ResearchModel([lambda payload: final(payload, answer='Прибыль 50.'), corrected])
    result = review.question_card(source_card(), 'Какова прибыль?', model, 'q', lambda: None, history=[])
    assert result['state'] == 'complete', result
    assert len(model.calls) == 2


def test_unexpected_model_exception_is_not_hidden_by_correction():
    model = ResearchModel([RuntimeError('Unexpected adapter bug'), final])
    with pytest.raises(RuntimeError, match='Unexpected adapter bug'):
        review.question_card(source_card(), 'Объясни расчёт.', model, 'q', lambda: None, history=[])
    assert len(model.calls) == 1
