from copy import deepcopy
import json
from uuid import UUID, uuid4

from test_ai_radar_runs import API, completed, run_scenario


def test_read_projects_prices_and_score_without_rewriting_saved_card(run_scenario):
    s = run_scenario
    receipt, result = completed(s)
    repository = s.service.run_pipeline.repository
    saved = deepcopy(repository.cards(UUID(receipt['run_id'])))
    card = result['cards'][0]
    assert isinstance(card['opportunity']['score'], int)
    assert 0 <= card['opportunity']['score'] <= 100
    assert all('unit_prices' in row for row in card['analysis']['positions'])
    assert card['report_summary']['paragraphs']
    assert s.client.get(API + '/' + receipt['run_id']).status_code == 200
    assert repository.cards(UUID(receipt['run_id'])) == saved


class HistoryReader:
    def generate(self, request_id, prompt, **kwargs):
        payload = json.loads(prompt.split('\nВХОД\n', 1)[1])
        action = dict(action='read', ref='history:', query='', offset=0, length=16000, notes='', response=None)
        if payload.get('tool_result'):
            history = json.loads(payload['tool_result']['content'])
            answer = ' '.join(row['question'] for row in history)
            action.update(action='final', ref='', response=dict(
                answer=answer, recommendation='Решение требует уточнения затрат и сроков.',
                risks=[], assumptions=[], evidence_refs=['history:'], calculated=payload['calculated'],
                decision='review', proposes_production=False, calculations=[]))
        return {'answer': json.dumps(action, ensure_ascii=False), 'model': 'history-reader'}


def test_question_reads_only_its_tender_history_through_real_queue(run_scenario):
    s = run_scenario
    receipt, result = completed(s)
    card = result['cards'][0]
    repository = s.service.run_pipeline.repository
    run_id = UUID(receipt['run_id'])
    repository.report(uuid4(), run_id, {'kind': 'question', 'analysis_id': card['analysis_id'],
        'question': 'Стоимость доставки 777 RUB.', 'result': {'state': 'complete', 'answer': 'Учтено.'}})
    repository.report(uuid4(), run_id, {'kind': 'question', 'analysis_id': result['cards'][1]['analysis_id'],
        'question': 'Секрет другого тендера.', 'result': {'state': 'complete', 'answer': 'Не передавать.'}})
    s.service.run_pipeline.model = HistoryReader()
    request = s.client.post(API + '/' + receipt['run_id'] + '/question', json={
        'command_key': 'question-history', 'analysis_id': card['analysis_id'], 'question': 'Что я уточнил ранее?'})
    assert request.status_code == 202
    assert s.service.run_once()
    report = next(row['data'] for row in repository.read_reports(run_id)
                  if str(row['job_id']) == request.json()['job_id'])
    assert report['result']['state'] == 'complete', report
    assert report['result']['answer'] == 'Стоимость доставки 777 RUB.'


def test_refreshed_review_is_a_separate_report_and_preserves_original(run_scenario):
    s = run_scenario
    receipt, result = completed(s)
    run_id = UUID(receipt['run_id'])
    repository = s.service.run_pipeline.repository
    card = result['cards'][0]
    before = deepcopy(repository.cards(run_id))
    review = {'state': 'complete', 'prompt_version': 'card-review-v5', 'recommendation': 'Проверены реальные условия изготовления.',
              'decision': 'review', 'risks': [], 'assumptions': [], 'evidence_refs': ['fulfillment']}
    repository.report(uuid4(), run_id, {'kind': 'review', 'analysis_id': card['analysis_id'],
                                      'projection_version': 1, 'result': review})
    actual = s.client.get(API + '/' + receipt['run_id']).json()['cards'][0]
    assert actual['review'] == review
    assert actual['projection']['review_stale'] is False
    assert repository.cards(run_id) == before


def test_review_operation_runs_research_and_preserves_original(run_scenario):
    s = run_scenario
    receipt, result = completed(s)
    run_id = UUID(receipt['run_id'])
    repository = s.service.run_pipeline.repository
    card = result['cards'][0]
    before = deepcopy(repository.cards(run_id))
    response = s.client.post(API + '/' + receipt['run_id'] + '/review', json={
        'command_key': 'refresh-analytical-report', 'analysis_id': card['analysis_id']})
    assert response.status_code == 202, response.text
    assert s.service.run_once()
    report = next(row['data'] for row in repository.read_reports(run_id)
                  if str(row['job_id']) == response.json()['job_id'])
    assert report['result']['state'] == 'complete', report
    assert report['result']['prompt_version'] == 'card-review-v5'
    actual = s.client.get(API + '/' + receipt['run_id']).json()['cards'][0]
    assert actual['review'] == report['result']
    assert actual['projection']['review_stale'] is False
    assert repository.cards(run_id) == before
