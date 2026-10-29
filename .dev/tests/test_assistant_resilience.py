"""HTTP workflows retain real effects and expose recoverable tool failure receipts.

Only the external model and embedding provider are simulated. Chat persistence,
authorization boundaries, document reads, action dispatch, receipts and PDF bytes
use their production implementations in the dedicated PostgreSQL test schema.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
import subprocess
from uuid import UUID

import pytest

from bervel.core.api.app import create_platform_app
from bervel.core.config import Settings
from bervel.core.document_memory import DocumentMemory
from bervel.core.errors import CoreError
from bervel.core.infrastructure.document_reader import DocumentReader
from bervel.core.infrastructure.documents import PostgresDocumentRepository
from bervel.core.infrastructure.knowledge import PostgresKnowledgeRepository
from bervel.core.knowledge import KnowledgeService
from bervel.core.models import ArtifactRef, RegisterArtifact
from bervel.modules.chat import ChatService
from bervel.modules.chat.api import chat_router
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from http_client import ASGITestClient
from test_assistant_workflows import actor_with_chat, build_actions, content


HEADERS = {'Authorization': 'Bearer ' + 'a' * 32}
QUESTION = 'проанализируй наших конкурентов и составь документ пдф с анализом их производства'
WEB_URL = 'https://manufacturer.example/production'
SOURCE_TEXT = 'BERVEL manufactures M16 fasteners by cold heading. Capacity: 120 tonnes per month.'
WEB_FACT = 'Competitor Zeta has 3 forging lines; actual monthly output is not published.'


def plan(operation, arguments, quote=''):
    return {'answer': json.dumps({'answer': '', 'actions': [{'operation': operation,
        'arguments': arguments, 'authorization_quote': quote}]}, ensure_ascii=False), 'model': 'fixture'}


def answer(text):
    return {'answer': json.dumps({'answer': text, 'actions': []}, ensure_ascii=False), 'model': 'fixture'}


def request_payload(prompt):
    return json.loads(prompt.split('\n', 1)[1])


def receipt(entry):
    """Failures have typed receipts; result cards remain completed effects only."""
    result = entry.get('_receipt', {})
    error = entry.get('error') or result.get('error') or {}
    return result, error


def document(**changes):
    return {'format': 'pdf', 'filename': 'analysis.pdf', 'title': 'Production analysis',
        'content': 'User supplied production facts.', **changes}


class ExternalEmbeddings:
    space = 'text-embedding-3-small:1536'
    dimensions = 1536
    max_tokens = 8192

    def count_tokens(self, value):
        return len(value.encode())

    def embed(self, texts, **options):
        return [[1.0, *([0.0] * 1535)] for _ in texts]


@contextmanager
def http_chat(services, pg_engine, model, *, memory=None, actor=None):
    actor = actor or actor_with_chat(services)
    documents = PostgresDocumentRepository(pg_engine)
    memory = memory or DocumentMemory(services[0], documents, DocumentReader())
    actions = build_actions(services, pg_engine)
    repository = PostgresChatRepository(pg_engine, documents)
    service = ChatService(repository, memory, model, actions=actions)
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), services[2].root,
        {'a' * 32: actor})
    app, dependency = create_platform_app(settings, pg_engine, services[0])
    app.include_router(chat_router(service, dependency))
    with ASGITestClient(app) as http:
        created = http.post('/api/v0.1/chat/sessions', headers=HEADERS, json={'command_key': 'session'})
        assert created.status_code == 200
        session_path = '/api/v0.1/chat/sessions/' + created.json()['session_id']

        def send(question, key='question'):
            sent = http.post(session_path + '/messages', headers=HEADERS,
                json={'command_key': key, 'content': question})
            assert sent.status_code == 202
            return sent.json()

        def read():
            response = http.get(session_path, headers=HEADERS)
            assert response.status_code == 200
            return response.json()['turns'][0]

        yield http, service, actions, actor, send, read


def test_recoverable_search_failure_reaches_real_read_web_and_sourced_pdf(services, pg_engine, tmp_path):
    """A failed retrieval must not turn a useful five-operation request into failed chat."""
    actor = actor_with_chat(services)
    original = services[0].register(actor,
        RegisterArtifact(SOURCE_TEXT.encode(), 'bervel-production.txt', 'text/plain', {}, 'seed'))
    index = PostgresKnowledgeRepository(pg_engine)
    external = ExternalEmbeddings()
    knowledge = KnowledgeService(index, services[0], DocumentReader(), external)
    while knowledge.run_once():
        pass
    # A real configuration mismatch is independent of the embedding-outage lexical fallback.
    external.space = 'replacement-embedding-model:1536'
    memory = DocumentMemory(services[0], index, DocumentReader(), knowledge=knowledge)

    class Model:
        def generate(self, request_id, prompt, **options):
            if options.get('web_search'):
                return {'answer': WEB_FACT, 'model': 'native-search',
                    'sources': [{'title': 'Zeta production', 'url': WEB_URL}]}
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            results = value['ACTION_RESULTS']
            if not results:
                first = plan('knowledge.search', {'query': 'BERVEL competitors production'})
                payload = json.loads(first['answer'])
                payload['task'] = {'deliverable': 'file', 'formats': ['pdf'], 'research_required': True}
                first['answer'] = json.dumps(payload)
                return first
            failure, error = receipt(results[0])
            assert failure['operation'] == 'knowledge.search' and failure['state'] == 'failed'
            assert error['code'] == 'KNOWLEDGE_REINDEX_REQUIRED'
            if len(results) == 1:
                return plan('file.list', {})
            if len(results) == 2:
                selected = results[1]['data']['artifacts'][0]
                assert selected['filename'] == 'bervel-production.txt'
                return plan('file.read_page', {'artifact_id': selected['artifact_id']})
            if len(results) == 3:
                assert results[2]['data']['sources'][0]['excerpt'] == SOURCE_TEXT
                return plan('web.search', {'query': 'Zeta fastener production official manufacturing'}, QUESTION)
            if len(results) == 4:
                local = results[2]['data']['sources'][0]
                web = results[3]
                assert web['kind'] == 'web' and web['sources'][0]['url'] == WEB_URL
                provenance = f"{local['filename']}, {local['unit']} {local['row_start']}-{local['row_end']}"
                return plan('file.create', document(content=(local['excerpt'] + '\nSource: ' + provenance
                    + '\n\n' + web['answer'] + '\nSource: ' + web['sources'][0]['url'])), QUESTION)
            assert results[4]['_receipt']['state'] == 'completed'
            return answer('PDF saved; semantic search was unavailable; exact file and public source were used.')

    with http_chat(services, pg_engine, Model(), memory=memory) as (http, service, actions, actor, send, read):
        sent = send(QUESTION)
        assert service.run_once()
        turn = read()
        assert turn['actions'][0]['state'] == 'failed'
        assert turn['actions'][0]['error']['code'] == 'KNOWLEDGE_REINDEX_REQUIRED'
        assert turn['state'] == 'partial', turn['error']
        assert turn['error'] is None and turn['answer']
        assert [(row['operation'], row['state']) for row in turn['actions']] == [
            ('knowledge.search', 'failed'), ('file.list', 'completed'), ('file.read_page', 'completed'),
            ('web.search', 'completed'), ('file.create', 'completed')]
        durable = actions.receipts.read(actor, UUID(sent['turn_id']))
        assert durable[0]['error']['code'] == 'KNOWLEDGE_REINDEX_REQUIRED'
        assert durable[0]['result'] is None
        assert all(result.get('kind') != 'failure' for result in turn['results'])
        assert turn['sources'][0]['artifact_id'] == str(original.artifact_id)
        assert turn['sources'][0]['filename'] == 'bervel-production.txt'
        cards = [result for result in turn['results'] if result.get('kind') == 'file']
        assert len(cards) == 1
        downloaded = http.get(cards[0]['download_url'], headers=HEADERS)
        assert downloaded.status_code == 200 and downloaded.content.startswith(b'%PDF-')
        pdf = tmp_path / 'production-analysis.pdf'
        pdf.write_bytes(downloaded.content)
        extracted = subprocess.check_output(['pdftotext', str(pdf), '-']).decode()
        assert '120 tonnes per month' in extracted
        assert '3 forging lines' in extracted
        assert 'actual monthly output is not published' in extracted
        assert 'bervel-production.txt' in extracted and 'line 1-1' in extracted and WEB_URL in extracted
        assert len(services[0].list(actor)['artifacts']) == 2
        assert not service.run_once()


@pytest.mark.parametrize('failure_code', ['MODEL_FAILED', 'MODEL_TIMEOUT', 'MODEL_INVALID_OUTPUT'])
def test_model_failure_after_create_keeps_one_real_pdf_and_conversational_answer(
        services, pg_engine, failure_code):
    followups = []
    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if not value['ACTION_RESULTS']:
                return plan('file.create', document(content='Actual user text: 120 tonnes.'), 'Создай PDF')
            followups.append(request_id)
            raise CoreError(failure_code, 'private upstream diagnostic must never be persisted')

    with http_chat(services, pg_engine, Model()) as (http, service, actions, actor, send, read):
        sent = send('Создай PDF с текстом: Actual user text: 120 tonnes.')
        assert service.run_once()
        turn = read()
        assert len(turn['results']) == 1 and turn['results'][0]['kind'] == 'file'
        assert len(services[0].list(actor)['artifacts']) == 1
        assert turn['state'] == 'completed', turn['error']
        assert turn['error'] is None and turn['answer']
        assert any(word in turn['answer'].casefold() for word in ('файл', 'документ', 'pdf'))
        assert followups == [], 'A saved file is verified locally without another model request'
        assert turn['workflow']['phase'] == 'completed'
        assert 'private upstream' not in json.dumps(turn)
        assert len(turn['results']) == 1 and turn['results'][0]['kind'] == 'file'
        downloaded = http.get(turn['results'][0]['download_url'], headers=HEADERS)
        assert downloaded.status_code == 200 and downloaded.content.startswith(b'%PDF-')
        durable = actions.receipts.read(actor, UUID(sent['turn_id']))
        assert len(durable) == 1 and durable[0]['state'] == 'completed'
        assert len(services[0].list(actor)['artifacts']) == 1
        assert not service.run_once()
        assert read()['results'] == turn['results']


@pytest.mark.parametrize('web_failure', ['WEB_SEARCH_UNAVAILABLE', 'WEB_SEARCH_TIMEOUT', 'invalid_sources'])
def test_web_failure_is_an_honest_conversation_without_invented_source_or_file(services, pg_engine, web_failure):
    class Model:
        def generate(self, request_id, prompt, **options):
            if options.get('web_search'):
                if web_failure == 'invalid_sources':
                    return {'answer': 'UNSUPPORTED INVENTED FACT', 'model': 'native-search',
                        'sources': [{'title': 'Invalid', 'url': 'javascript:alert(1)'}]}
                raise CoreError(web_failure, 'private provider response body')
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if not value['ACTION_RESULTS']:
                return plan('web.search', {'query': 'Zeta manufacturing official'}, 'Найди в интернете')
            failed, error = receipt(value['ACTION_RESULTS'][0])
            assert failed['state'] == 'failed' and failed['operation'] == 'web.search'
            assert error['code'] == ('WEB_SEARCH_FAILED' if web_failure == 'invalid_sources' else web_failure)
            return answer('Не удалось получить проверяемые источники. Данных для анализа нет; документ не создан.')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        sent = send('Найди в интернете сведения о производстве Zeta и создай PDF')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked', turn['error']
        assert turn['answer'] and turn['error'] is None
        assert turn['sources'] == [] and turn['results'] == []
        assert len(turn['actions']) == 1 and turn['actions'][0]['state'] == 'failed'
        assert turn['actions'][0]['error']['code'] == (
            'WEB_SEARCH_FAILED' if web_failure == 'invalid_sources' else web_failure)
        assert 'UNSUPPORTED INVENTED FACT' not in json.dumps(turn)
        assert 'private provider' not in json.dumps(turn)
        assert actions.receipts.read(actor, UUID(sent['turn_id'])) == turn['actions']
        assert services[0].list(actor)['artifacts'] == []


def test_failed_receipt_survives_large_real_read_page_projection_without_truncating_durable_data(
        services, pg_engine):
    actor = actor_with_chat(services)
    large_text = '\n'.join(f'Production record {number}: ' + ('steel fasteners ' * 80) for number in range(200))
    original = services[0].register(actor,
        RegisterArtifact(large_text.encode(), 'large-production.txt', 'text/plain', {}, 'seed'))
    full_arguments = document(filename='../analysis.pdf', content='User supplied facts. ' * 1100)

    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            assert len(prompt.encode()) <= 192 * 1024
            results = value['ACTION_RESULTS']
            if not results:
                return plan('file.read_page', {'artifact_id': str(original.artifact_id), 'limit': 100})
            page = results[0]['data']
            assert page['shown'] < 100 and page['truncated'] is True
            assert page['next_offset'] == page['shown']
            if len(results) == 1:
                return plan('file.create', full_arguments, 'Создай PDF')
            failed, error = receipt(results[1])
            assert failed['operation'] == 'file.create' and failed['state'] == 'failed'
            assert error['code'] == 'VALIDATION_ERROR' and error['message']
            return answer('Прочитана часть файла. PDF не создан: имя файла не прошло проверку.')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        sent = send('Создай PDF по тексту пользователя и прочитай large-production.txt')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked', turn['error']
        durable = actions.receipts.read(actor, UUID(sent['turn_id']))
        assert [row['state'] for row in durable] == ['completed', 'failed']
        assert durable[1]['arguments'] == full_arguments
        assert durable[1]['error']['code'] == 'VALIDATION_ERROR'
        assert durable[0]['result']['data']['shown'] == 100
        assert len(durable[0]['result']['data']['sources']) == 100
        assert len(turn['results']) == 1 and turn['results'][0] == durable[0]['result']
        assert len(services[0].list(actor)['artifacts']) == 1
        assert not service.run_once()


@pytest.mark.parametrize('bad_filename', ['../analysis.pdf', 'analysis.txt'])
def test_known_filename_rejection_can_be_corrected_under_a_new_action_key(services, pg_engine, bad_filename):
    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            results = value['ACTION_RESULTS']
            if not results:
                return plan('file.create', document(filename=bad_filename), 'Создай PDF')
            if len(results) == 1:
                failed, error = receipt(results[0])
                assert failed['state'] == 'failed' and error['code'] == 'VALIDATION_ERROR'
                return plan('file.create', document(), 'Создай PDF')
            return answer('Документ сохранён с исправленным именем.')

    with http_chat(services, pg_engine, Model()) as (http, service, actions, actor, send, read):
        sent = send('Создай PDF с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'completed', turn['error']
        durable = actions.receipts.read(actor, UUID(sent['turn_id']))
        assert [row['state'] for row in durable] == ['failed', 'completed']
        assert durable[0]['action_id'] != durable[1]['action_id']
        assert durable[0]['error']['code'] == 'VALIDATION_ERROR' and durable[0]['result'] is None
        assert len(turn['results']) == 1 and turn['results'][0]['filename'] == 'analysis.pdf'
        assert http.get(turn['results'][0]['download_url'], headers=HEADERS).content.startswith(b'%PDF-')
        assert len(services[0].list(actor)['artifacts']) == 1


def test_identical_failed_mutation_signature_is_stopped_without_new_effect_keys(services, pg_engine):
    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            return plan('file.create', document(filename='../analysis.pdf'), 'Создай PDF')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        send('Создай PDF с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked', turn['error']
        assert turn['answer'] and turn['results'] == []
        assert len(turn['actions']) == 1 and turn['actions'][0]['state'] == 'failed'
        assert services[0].list(actor)['artifacts'] == []
        assert not service.run_once()


def test_completed_mutation_signature_is_never_executed_twice(services, pg_engine):
    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            return plan('file.create', document(), 'Создай PDF')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        send('Создай PDF с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        assert len(turn['actions']) == 1 and turn['actions'][0]['state'] == 'completed'
        assert len(turn['results']) == 1 and turn['results'][0]['kind'] == 'file'
        assert len(services[0].list(actor)['artifacts']) == 1
        assert not service.run_once()


def test_forbidden_effect_is_a_hard_stop_without_alternative_tool_bypass(services, pg_engine):
    actor = actor_with_chat(services)
    actor = replace(actor, permissions=actor.permissions - {'artifact:write'})

    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if value['ACTION_RESULTS']:
                # This alternative must never be reached after a permission failure.
                return plan('ticket.create', content(), 'Создай PDF или обращение')
            return plan('file.create', document(), 'Создай PDF')

    with http_chat(services, pg_engine, Model(), actor=actor) as (_, service, actions, actor, send, read):
        send('Создай PDF или обращение с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked' and turn['results'] == []
        assert len(turn['actions']) == 1 and turn['actions'][0]['error']['code'] == 'FORBIDDEN'
        assert services[0].list(actor)['artifacts'] == []
        assert actions.tickets.list(actor)['tickets'] == []


def test_unknown_effect_stops_without_alternative_tool_or_automatic_replay(services, pg_engine):
    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if value['ACTION_RESULTS']:
                return plan('ticket.create', content(), 'Создай PDF или обращение')
            return plan('file.create', document(), 'Создай PDF')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        sent = send('Создай PDF или обращение с текстом User supplied production facts.')
        # Durable running intent models loss of the response after external dispatch.
        actions.receipts.begin(actor, UUID(sent['turn_id']), 0, 'file.create', document())
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked' and turn['results'] == []
        assert len(turn['actions']) == 1 and turn['actions'][0]['state'] == 'unknown'
        assert services[0].list(actor)['artifacts'] == []
        assert actions.tickets.list(actor)['tickets'] == []
        assert not service.run_once()


@pytest.mark.parametrize('unavailable_model', [None, 'MODEL_FAILED'])
def test_unavailable_model_before_effect_gives_truthful_answer_without_success_cards(
        services, pg_engine, unavailable_model):
    class Model:
        def generate(self, *_args, **_options):
            raise CoreError('MODEL_FAILED', 'private upstream diagnostic')

    model = None if unavailable_model is None else Model()
    with http_chat(services, pg_engine, model) as (_, service, actions, actor, send, read):
        send('Создай PDF с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked', turn['error']
        assert turn['answer'] and turn['error'] is None
        assert any(word in turn['answer'].casefold() for word in ('недоступ', 'не удалось', 'не заверш'))
        assert turn['results'] == [] and turn['actions'] == [] and turn['sources'] == []
        assert 'private upstream' not in json.dumps(turn)
        assert services[0].list(actor)['artifacts'] == []
