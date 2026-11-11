from copy import deepcopy
import json
from urllib.parse import quote
from uuid import UUID

import pytest

from test_ai_radar_runs import API, completed, run_scenario


class RefreshModel:
    def generate(self, request_id, prompt, **kwargs):
        payload = json.loads(prompt.split('\nВХОД\n', 1)[1])
        action = dict(action='refresh', ref='', offset=0, length=16000, query='', notes='', response=None)
        if payload.get('tool_result'):
            action.update(action='final', response=dict(
                recommendation='Данные исходной карточки доступны.', answer='Исходная карточка обновлена.',
                risks=[], assumptions=[], evidence_refs=['refreshed:'], calculated=payload['calculated'],
                decision='review', proposes_production=False, calculations=[]))
        return {'model': 'refresh-model', 'answer': json.dumps(action, ensure_ascii=False)}


@pytest.mark.parametrize('purchase_stages', [None, ['cancelled']])
def test_refresh_allows_cancelled_source_only_for_explicit_cancelled_run(run_scenario, monkeypatch, purchase_stages):
    s = run_scenario
    receipt, result = completed(s)
    pipeline = s.service.run_pipeline
    repository = pipeline.repository
    run_id = UUID(receipt['run_id'])
    card = result['cards'][0]
    original_cards = deepcopy(repository.cards(run_id))
    if purchase_stages:
        run = repository.read(s.actor.data_scope, run_id)
        repository.update(s.actor.data_scope, run_id, dict(run['data'], purchase_stages=purchase_stages))

    raw = deepcopy(card['tender']['source_card'])
    raw.update(CancelReason='Закупка отменена', Docs=[])
    requested = []

    def request(path, *args, **kwargs):
        requested.append(path)
        assert path == '/external/v1/purchases/' + quote(card['candidate_id'], safe='')
        return deepcopy(raw)

    monkeypatch.setattr(pipeline.sources['test_api'], '_request', request)
    pipeline.model = RefreshModel()
    receipt = s.client.post(API + '/' + str(run_id) + '/question', json={
        'command_key': 'refresh-bound-source', 'analysis_id': card['analysis_id'],
        'question': 'Обнови исходную карточку закупки.'})
    assert receipt.status_code == 202
    assert s.service.run_once()
    report = next(entry['data'] for entry in repository.read_reports(run_id)
                  if str(entry['job_id']) == receipt.json()['job_id'])
    assert requested == ['/external/v1/purchases/' + quote(card['candidate_id'], safe='')]
    if purchase_stages:
        assert report['result']['state'] == 'complete', report
        assert report['result']['answer'] == 'Исходная карточка обновлена.'
    else:
        assert report['result']['state'] == 'failed'
        assert report['result']['error']['code'] == 'KONTUR_TENDER_CLOSED'
    assert repository.cards(run_id) == original_cards
