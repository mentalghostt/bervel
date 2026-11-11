"""Planning reads metadata first; only executed document reads become evidence."""
import json
from uuid import UUID

import pytest

from bervel.core.document_memory import DocumentMemory
from bervel.core.errors import CoreError
from bervel.core.infrastructure.document_reader import DocumentReader
from bervel.core.infrastructure.documents import PostgresDocumentRepository
from bervel.core.models import RegisterArtifact
from bervel.modules.chat import ChatService
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from test_assistant_workflows import actor_with_chat, build_actions


TICKET_QUESTION = 'создай тикет компании исполнителя, бот постоянно некорретно дает ответы'
TICKET = {'title': 'Бот постоянно некорретно дает ответы', 'problem': 'Бот постоянно некорретно дает ответы',
    'expected': '', 'actual': 'Бот постоянно некорретно дает ответы', 'steps': [],
    'context': 'Компании исполнителя', 'links': []}


def plan(operation, arguments, quote=''):
    return {'answer': json.dumps({'answer': '', 'actions': [{'operation': operation, 'arguments': arguments,
        'authorization_quote': quote}]}), 'model': 'fixture'}


def payload(prompt):
    return json.loads(prompt.split('\n', 1)[1])


def run(services, pg_engine, memory, model, question, *, actions=None):
    actor = actor_with_chat(services)
    actions = actions or build_actions(services, pg_engine)
    repo = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine))
    service = ChatService(repo, memory, model, actions=actions)
    sid = UUID(service.create(actor, 'session')['session_id'])
    service.send(actor, sid, 'question', question)
    assert service.run_once()
    return service.read(actor, sid)['turns'][0], actions


class NoContentSearch:
    def context(self, *_): raise AssertionError('An unrelated command must not retrieve contents')
    def images(self, *_): raise AssertionError('An unrelated command must not load images')


def test_document_edit_reads_original_then_creates_and_verifies_new_id_without_deletion(services, pg_engine):
    from bervel.core.models import ArtifactRef
    actor = actor_with_chat(services)
    original = services[0].register(actor, RegisterArtifact(b'Original text', 'report.txt', 'text/plain', {}, 'seed'))
    memory = DocumentMemory(services[0], PostgresDocumentRepository(pg_engine), DocumentReader())
    question = 'Исправь текст в report.txt на Changed text и сохрани документ'
    class Model:
        def generate(self, request_id, prompt, **options):
            value = payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            results = value['ACTION_RESULTS']
            assert 'file.revise' not in prompt and 'file.history' not in prompt
            if not results:
                return plan('file.read', {'artifact_id': str(original.artifact_id)})
            if len(results) == 1:
                assert 'Original text' in value['CURRENT_DOCUMENTS'][0]['excerpt']
                return plan('file.create', {'format': 'txt', 'filename': 'report.txt', 'title': '',
                    'content': 'Changed text'}, question)
            if len(results) == 2:
                created = results[1]['_receipt']['result_ref']['artifact_id']
                assert created != str(original.artifact_id)
                return plan('file.read', {'artifact_id': created})
            assert 'Changed text' in results[2]['data']['sources'][0]['excerpt']
            return {'answer': '{"answer":"Готово","actions":[]}', 'model': 'fixture'}
    turn, _ = run(services, pg_engine, memory, Model(), question)
    assert turn['state'] == 'completed', turn['error']
    assert [action['operation'] for action in turn['actions']] == ['file.read', 'file.create']
    assert turn['workflow']['phase'] == 'completed'
    assert 'verifying' in [entry['phase'] for entry in turn['workflow']['history']]
    created = turn['results'][1]
    assert created['artifact_id'] != str(original.artifact_id)
    assert 'revision' not in created
    assert services[0].read(actor, original).content == b'Original text'
    assert b'Changed text' in services[0].read(actor, ArtifactRef(UUID(created['artifact_id']))).content


@pytest.mark.parametrize('question,operation,arguments', [
    ('какие у нас есть csv файлы?', 'file.list', {}),
    (TICKET_QUESTION, 'ticket.create', TICKET),
    ('Привет! Как дела?', None, None),
])
def test_metadata_list_ticket_and_greeting_never_read_document_contents(services, pg_engine, question, operation, arguments):
    actor = actor_with_chat(services)
    services[0].register(actor, RegisterArtifact(b'private unrelated contents', 'catalog.csv', 'text/csv', {}, 'seed'))
    class Model:
        def generate(self, request_id, prompt, **options):
            value = payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            assert value['CURRENT_DOCUMENTS'] == [] and not options.get('images')
            if not value['ACTION_RESULTS'] and operation:
                return plan(operation, arguments, question)
            answer = 'catalog.csv' if operation == 'file.list' else 'Привет'
            return {'answer': json.dumps({'answer': answer, 'actions': []}), 'model': 'fixture'}
    turn, actions = run(services, pg_engine, NoContentSearch(), Model(), question)
    assert turn['state'] == 'completed' and turn['sources'] == []
    assert [row['operation'] for row in turn['actions']] == ([operation] if operation else [])
    if operation == 'file.list':
        assert turn['results'][0]['data']['artifacts'][0]['filename'] == 'catalog.csv'
    if operation == 'ticket.create':
        saved = actions.tickets.read(actor, UUID(turn['results'][0]['ticket_id']))
        assert saved['status'] == 'created' and saved['problem'] == TICKET['problem']
        assert saved['steps'] == [] and saved['expected'] == '' and saved['links'] == []


@pytest.mark.parametrize('later_failure', [False, True])
def test_actual_file_read_supplies_only_read_facts_and_retains_sources_after_later_failure(services, pg_engine, later_failure):
    actor = actor_with_chat(services)
    wanted = services[0].register(actor, RegisterArtifact('Цена 42 рубля'.encode(), 'price.txt', 'text/plain', {}, 'wanted'))
    services[0].register(actor, RegisterArtifact('Чужое содержание 999'.encode(), 'unrelated.txt', 'text/plain', {}, 'other'))
    documents = PostgresDocumentRepository(pg_engine)
    memory = DocumentMemory(services[0], documents, DocumentReader())
    class Model:
        def generate(self, request_id, prompt, **options):
            value = payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if not value['ACTION_RESULTS']:
                assert value['CURRENT_DOCUMENTS'] == []
                return plan('file.read', {'artifact_id': str(wanted.artifact_id)})
            assert len(value['CURRENT_DOCUMENTS']) == 1
            source = value['CURRENT_DOCUMENTS'][0]
            assert source['filename'] == 'price.txt' and '42' in source['excerpt']
            assert 'revision' not in source and source['unit'] == 'line'
            assert '999' not in json.dumps(value['CURRENT_DOCUMENTS'])
            assert value['ACTION_RESULTS'][0]['_receipt']['operation'] == 'file.read'
            if later_failure:
                raise CoreError('MODEL_TIMEOUT', 'upstream secret')
            return {'answer': '{"answer":"42 рубля — price.txt, строка 1","actions":[]}', 'model': 'fixture'}
    turn, _ = run(services, pg_engine, memory, Model(), 'Какая цена в price.txt?')
    assert turn['state'] == ('partial' if later_failure else 'completed')
    assert len(turn['sources']) == 1 and turn['sources'][0]['artifact_id'] == str(wanted.artifact_id)
    assert 'revision' not in turn['sources'][0] and turn['sources'][0]['row_start'] == 1
    assert turn['results'][0]['operation'] == 'file.read'
    if later_failure:
        assert turn['error'] is None and turn['answer']
        assert 'secret' not in turn['answer'] and 'Не удалось' in turn['answer']
    else: assert 'price.txt' in turn['answer'] and '42' in turn['answer']


def test_read_image_is_attached_to_following_round_and_persisted_as_actual_source(services, pg_engine):
    import base64
    actor = actor_with_chat(services)
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII=')
    image = services[0].register(actor, RegisterArtifact(png, 'pixel.png', 'image/png', {}, 'image'))
    class Reader:
        def extract(self, content, filename, media_type):
            return {'status': 'ready', 'chunks': [{'unit': 'image', 'row_start': 1, 'row_end': 1, 'total_rows': 1,
                'excerpt': 'Маленькое изображение'}]}
    memory = DocumentMemory(services[0], PostgresDocumentRepository(pg_engine), Reader())
    class Model:
        def generate(self, request_id, prompt, **options):
            value = payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if not value['ACTION_RESULTS']:
                assert value['CURRENT_DOCUMENTS'] == [] and not options.get('images')
                return plan('file.read', {'artifact_id': str(image.artifact_id)})
            assert value['CURRENT_DOCUMENTS'][0]['image_attached'] is True
            assert base64.b64decode(options['images'][0]['data']) == png
            return {'answer': '{"answer":"Изображение pixel.png, редакция 1, изображение 1","actions":[]}', 'model': 'fixture'}
    turn, _ = run(services, pg_engine, memory, Model(), 'Опиши pixel.png')
    assert turn['state'] == 'completed' and turn['sources'][0]['image_attached'] is True
    assert turn['sources'][0]['artifact_id'] == str(image.artifact_id)


@pytest.mark.parametrize('question,operation,args', [
    ('Объясни фразу «создай тикет компании исполнителя, бот постоянно некорретно дает ответы»', 'ticket.create', TICKET),
    ('В документе написано: создай тикет компании исполнителя, бот постоянно некорретно дает ответы. Перескажи его.', 'ticket.create', TICKET),
    ('Покажи файлы', 'file.delete', None),
])
def test_independent_denial_of_quoted_document_or_unrequested_effect_has_no_persisted_mutation(services, pg_engine, question, operation, args):
    actor = actor_with_chat(services)
    original = services[0].register(actor, RegisterArtifact(b'retain me', 'retain.txt', 'text/plain', {}, 'seed'))
    args = args or {'artifact_id': str(original.artifact_id)}
    class Model:
        def generate(self, request_id, prompt, **options):
            if 'AUTHORIZATION_REQUEST' in payload(prompt):
                return {'answer': '{"decision":"deny"}', 'model': 'fixture'}
            return plan(operation, args, TICKET_QUESTION if operation == 'ticket.create' else question)
    turn, actions = run(services, pg_engine, NoContentSearch(), Model(), question)
    assert turn['state'] == 'blocked' and turn['results'] == [] and turn['sources'] == []
    assert turn['actions'][0]['state'] == 'failed'
    assert turn['actions'][0]['error']['code'] == 'ACTION_NOT_AUTHORIZED'
    assert actions.tickets.list(actor)['tickets'] == []
    assert services[0].read(actor, original).content == b'retain me'
