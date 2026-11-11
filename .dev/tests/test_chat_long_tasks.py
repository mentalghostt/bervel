"""Long task phases use real HTTP, PostgreSQL receipts, originals and PDF renderer."""
from dataclasses import replace
import importlib.util
import json
import subprocess
from uuid import UUID

import pytest

from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact
from test_assistant_resilience import HEADERS, QUESTION, WEB_URL, document, http_chat, request_payload
from test_assistant_workflows import actor_with_chat


FACT = 'BERVEL capacity: 120 tonnes per month. Fasteners: M16 bolts.'
WEB_FACT = 'Zeta has 3 forging lines; actual output is not published. Source: ' + WEB_URL
TASK = {'deliverable': 'file', 'formats': ['pdf'], 'research_required': True}


def response(actions=(), *, task=TASK, complete=False, answer='', limitations=()):
    return {'answer': json.dumps({'answer': answer, 'actions': list(actions), 'task': task,
        'research_complete': complete, 'limitations': list(limitations)}, ensure_ascii=False), 'model': 'external-fixture'}


def action(operation, arguments, quote=''):
    return {'operation': operation, 'arguments': arguments, 'authorization_quote': quote}


def legacy_or_modern(payload, actions, **options):
    """The old planner still reaches its actual limit, rather than failing on a new field."""
    if 'WORKFLOW_STATE' in payload:
        return response(actions, **options)
    return {'answer': json.dumps({'answer': '', 'actions': actions}), 'model': 'external-fixture'}


def seed(services):
    actor = actor_with_chat(services)
    reference = services[0].register(actor, RegisterArtifact(FACT.encode(), 'production.txt', 'text/plain', {}, 'source'))
    return actor, reference


def test_successful_research_budget_forces_synthesis_and_verified_real_pdf(services, pg_engine, tmp_path, monkeypatch):
    actor, reference = seed(services)
    observed, verified_in, web_calls = [], [], []
    read_turn = None
    original_read = services[0].read
    def observe_original(context, ref):
        value = original_read(context, ref)
        if value.metadata['filename'] == 'analysis.pdf' and read_turn:
            verified_in.append(read_turn().get('workflow', {}).get('phase'))
        return value
    monkeypatch.setattr(services[0], 'read', observe_original)

    class Model:
        def generate(self, request_id, prompt, **options):
            if options.get('web_search'):
                web_calls.append(request_id)
                return {'answer': WEB_FACT, 'model': 'native-web-fixture', 'sources': [
                    {'title': 'Zeta production', 'url': WEB_URL}, *[
                        {'title': f'Public source {number}', 'url': f'https://manufacturer.example/{number}'} for number in range(90)]]}
            payload = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in payload:
                return {'answer': '{"decision":"allow"}', 'model': 'external-fixture'}
            state = payload.get('WORKFLOW_STATE', {})
            phase = state.get('phase', 'research')
            if state:
                stored = read_turn()['workflow']
                assert stored['phase'] == phase, 'Phase must be durable before an external call'
                observed.append(phase)
            if phase == 'research':
                if not payload['ACTION_RESULTS']:
                    return legacy_or_modern(payload, [action('file.read_page', {'artifact_id': str(reference.artifact_id)})])
                return legacy_or_modern(payload, [action('web.search', {'query': 'Zeta production ' + str(len(payload['ACTION_RESULTS']))}, QUESTION)])
            assert phase == 'synthesizing', 'Generating/verifying must not start another research model call'
            evidence = json.dumps(payload.get('EVIDENCE_NOTEBOOK'), ensure_ascii=False)
            assert FACT in evidence and WEB_URL in evidence and '3 forging lines' in evidence
            return response([action('file.create', document(content=FACT + '\nproduction.txt, line 1\n' + WEB_FACT), QUESTION)],
                limitations=['Research budget reached; output remains unverified.'])

    with http_chat(services, pg_engine, Model(), actor=actor) as (http, service, actions, actor, send, read):
        read_turn = read
        sent = send(QUESTION)
        assert service.run_once()
        turn = read()
        files = [result for result in turn['results'] if result.get('kind') == 'file']
        assert files, 'A successful long investigation must reserve creation instead of ending with only source lists'
        assert len(files) == 1 and 0 < len(web_calls) <= 2
        assert turn['state'] == 'partial' and turn['error'] is None
        workflow = turn['workflow']
        assert workflow['phase'] == 'partial' and workflow['reason']
        phases = [entry['phase'] for entry in workflow['history']]
        assert phases == ['research', 'synthesizing', 'generating', 'verifying', 'partial']
        assert workflow['research_rounds'] <= workflow['max_research_rounds'] == 4
        assert workflow['web_searches'] == len(web_calls) <= workflow['max_web_searches'] == 2
        assert 'research' in observed and 'synthesizing' in observed and 'verifying' in verified_in
        original = http.get(files[0]['download_url'], headers=HEADERS)
        assert original.status_code == 200 and original.content.startswith(b'%PDF-')
        pdf = tmp_path / 'long-task.pdf'; pdf.write_bytes(original.content)
        text = subprocess.check_output(['pdftotext', str(pdf), '-']).decode()
        assert '120 tonnes' in text and '3 forging lines' in text and WEB_URL in text
        assert len(services[0].list(actor)['artifacts']) == 2
        assert not service.run_once()
        assert read()['results'] == turn['results']
        assert len(actions.receipts.read(actor, UUID(sent['turn_id']))) == len(turn['actions'])


def test_sufficient_research_enters_generation_without_spending_remaining_search_budget(services, pg_engine):
    actor, reference = seed(services)
    class Model:
        def generate(self, request_id, prompt, **options):
            assert not options.get('web_search')
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if value.get('WORKFLOW_STATE', {}).get('phase') == 'synthesizing':
                return response([action('file.create', document(content=FACT + '\nSource: production.txt line 1'), QUESTION)], complete=True)
            if not value['ACTION_RESULTS']:
                return legacy_or_modern(value, [action('file.read_page', {'artifact_id': str(reference.artifact_id)})])
            return legacy_or_modern(value, [], complete=True, answer='Facts collected; now create the requested document.')
    with http_chat(services, pg_engine, Model(), actor=actor) as (_, service, _, _, send, read):
        send(QUESTION)
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'completed' and any(result['kind'] == 'file' for result in turn['results'])
        assert [entry['phase'] for entry in turn['workflow']['history']] == ['research', 'synthesizing', 'generating', 'verifying', 'completed']


def test_unavailable_research_without_evidence_is_blocked_without_an_invented_pdf(services, pg_engine):
    web_calls = []
    class Model:
        def generate(self, request_id, prompt, **options):
            if options.get('web_search'):
                web_calls.append(request_id)
                raise CoreError('WEB_SEARCH_UNAVAILABLE', 'private provider diagnostic')
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            return legacy_or_modern(value, [action('web.search', {'query': 'Zeta production ' + str(len(value['ACTION_RESULTS']))}, QUESTION)])
    with http_chat(services, pg_engine, Model()) as (_, service, _, actor, send, read):
        send(QUESTION)
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked' and turn['workflow']['phase'] == 'blocked'
        assert turn['workflow']['reason'] and len(web_calls) == 1
        assert turn['results'] == [] and services[0].list(actor)['artifacts'] == []
        assert 'private provider' not in json.dumps(turn)


def test_known_file_creation_confirmation_loss_blocks_and_never_recreates(services, pg_engine, monkeypatch):
    metadata = services[0].metadata
    def lose_confirmation(actor, ref):
        result = metadata(actor, ref)
        if result['filename'] == 'analysis.pdf':
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'private response lost')
        return result
    monkeypatch.setattr(services[0], 'metadata', lose_confirmation)
    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            return legacy_or_modern(value, [action('file.create', document(), 'Создай PDF')],
                task={'deliverable': 'file', 'formats': ['pdf'], 'research_required': False}, complete=True)
    with http_chat(services, pg_engine, Model()) as (_, service, _, actor, send, read):
        send('Создай PDF с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked' and turn['workflow']['phase'] == 'blocked'
        assert len(turn['actions']) == 1 and turn['actions'][0]['state'] == 'unknown'
        assert len(services[0].list(actor)['artifacts']) == 1 and turn['results'] == []
        assert not service.run_once()


def test_notebook_keeps_earlier_evidence_after_more_results_without_shrinking_it():
    assert importlib.util.find_spec('bervel.modules.chat.workflow') is not None, 'Workflow evidence notebook is missing'
    from bervel.modules.chat.workflow import initial_workflow, remember, evidence_context
    workflow = initial_workflow()
    first = {'kind': 'web', 'answer': WEB_FACT, 'sources': [{'title': 'Production', 'url': WEB_URL}]}
    remember(workflow, first, 'web.search', 0)
    initial = evidence_context(workflow)
    for number in range(1, 16):
        remember(workflow, {'kind': 'data', 'operation': 'file.list', 'data': {'artifacts': []}}, 'file.list', number)
    assert evidence_context(workflow) == initial
    assert WEB_FACT in json.dumps(initial)
