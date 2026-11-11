"""Question messages are visible from the durable receipt before an answer exists."""
from uuid import UUID

from sqlalchemy import event, func, select

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.runs import reports
from test_ai_radar_runs import API, completed, run_scenario


def test_question_get_shows_queued_message_then_same_question_and_answer_without_duplicate(run_scenario):
    s = run_scenario
    receipt, run = completed(s)
    path = f'{API}/{receipt["run_id"]}'
    body = {'command_key': 'show-question-before-answer', 'analysis_id': run['cards'][0]['analysis_id'],
        'question': 'Почему этот тендер интересен предприятию?'}
    question = s.client.post(path + '/question', json=body)
    assert question.status_code == 202, question.text
    job_id = question.json()['job_id']
    before = len(s.model.calls)
    with s.engine.connect() as conn:
        saved_count = conn.scalar(select(func.count()).select_from(reports))
    queued = s.client.get(path).json()
    shown = [row for row in queued['reports'] if row['job_id'] == job_id]
    assert len(shown) == 1
    assert shown[0]['question'] == body['question']
    assert shown[0]['analysis_id'] == body['analysis_id']
    assert shown[0]['command_key'] == body['command_key']
    assert shown[0]['state'] == 'queued' and shown[0]['result'] is None
    assert len(s.model.calls) == before
    with s.engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(reports)) == saved_count
    assert s.service.run_once(lane='question')
    answered = s.client.get(path).json()
    shown = [row for row in answered['reports'] if row['job_id'] == job_id]
    assert len(shown) == 1 and shown[0]['question'] == body['question']
    assert shown[0]['command_key'] == body['command_key']
    assert shown[0]['result']['state'] == 'complete'
    assert shown[0]['result']['answer']
    calls = len(s.model.calls)
    assert s.client.get(path).json()['reports'] == answered['reports']
    assert len(s.model.calls) == calls
    assert s.client.get(path, headers={'Authorization': 'Bearer ' + 'b' * 32}).status_code == 404
    second = s.client.post(path + '/question', json=dict(body, command_key='failed-question-before-answer'))
    assert second.status_code == 202
    claimed = s.service.jobs.claim(lane='question')
    assert str(claimed['job_id']) == second.json()['job_id']
    running = next(row for row in s.client.get(path).json()['reports'] if row['job_id'] == second.json()['job_id'])
    assert running['state'] == 'running' and running['result'] is None
    s.service.jobs.fail(UUID(second.json()['job_id']), CoreError('USER_STOPPED', 'Question stopped'))
    failed = next(row for row in s.client.get(path).json()['reports'] if row['job_id'] == second.json()['job_id'])
    assert failed['question'] == body['question'] and failed['result'] is None
    assert failed['state'] == 'failed' and failed['error']['code'] == 'USER_STOPPED'


def test_reply_committed_between_read_queries_never_hides_the_user_question(run_scenario):
    s = run_scenario
    receipt, run = completed(s)
    run_id = UUID(receipt['run_id'])
    repository = s.service.run_pipeline.repository
    body = {'command_key': 'question-report-read-race', 'analysis_id': run['cards'][0]['analysis_id'],
        'question': 'Мой вопрос должен оставаться видимым.'}
    response = s.client.post(f'{API}/{run_id}/question', json=body)
    assert response.status_code == 202
    job_id = UUID(response.json()['job_id'])
    inserted = False

    def reply_after_first_select(conn, cursor, statement, parameters, context, many):
        nonlocal inserted
        if not inserted and 'FROM ai_radar_reports LEFT OUTER JOIN' in statement:
            inserted = True
            repository.report(job_id, run_id, {'kind': 'question', 'analysis_id': body['analysis_id'],
                'question': body['question'], 'result': {'state': 'complete', 'answer': 'Сохранённый ответ.'}})

    event.listen(s.engine, 'after_cursor_execute', reply_after_first_select)
    try:
        shown = [row for row in repository.read_reports(run_id) if row['job_id'] == job_id]
        assert inserted
        assert len(shown) == 1 and shown[0]['data']['question'] == body['question']
    finally:
        event.remove(s.engine, 'after_cursor_execute', reply_after_first_select)
    shown = [row for row in repository.read_reports(run_id) if row['job_id'] == job_id]
    assert len(shown) == 1 and shown[0]['data']['result']['answer'] == 'Сохранённый ответ.'
