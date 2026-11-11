from bervel.core.infrastructure.documents import PostgresDocumentRepository
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from bervel.modules.chat.service import ChatService
from bervel.core.document_memory import DocumentMemory
from bervel.core.infrastructure.document_reader import DocumentReader
from bervel.core.errors import CoreError
from bervel.core.models import RegisterArtifact
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from bervel.modules.chat.infrastructure.tables import chat_turns
from bervel.core.infrastructure.database import document_indexes


class Model:
    def __init__(self):
        self.calls = []

    def generate(self, request_id, prompt):
        self.calls.append((request_id, prompt))
        return {'answer': 'Ответ по реальному контексту', 'model': 'test-double'}


@pytest.fixture
def chat(services, pg_engine):
    artifacts, _, store, actor = services
    actor = replace(actor, permissions=actor.permissions | {'chat:read', 'chat:write'})
    repository = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine))
    model = Model()
    return ChatService(repository, DocumentMemory(artifacts, repository.documents, DocumentReader()), model), actor, artifacts, repository, model, store


def upload(artifacts, actor, content='id,extra\n1,Первый\n', name='file.csv', **changes):
    return artifacts.register(actor, RegisterArtifact(content.encode(), name, 'text/csv', {}, uuid4().hex, **changes))


def test_latest_independent_original_scope_persistence_and_idempotency(chat, pg_engine):
    service, actor, artifacts, repo, model, _ = chat
    first = upload(artifacts, actor)
    session = service.create(actor, 'new-session')
    assert service.create(actor, 'new-session')['session_id'] == session['session_id']
    sid = UUID(session['session_id'])
    old = service.send(actor, sid, 'one', 'Что в последнем csv?')
    assert old['state'] == 'queued'
    assert service.run_once()
    latest = upload(artifacts, actor, 'id,extra\n2,Второй\n')
    upload(artifacts, replace(actor, data_scope='foreign'), 'id,extra\n3,Чужой\n')
    artifacts.register(actor, RegisterArtifact(b'note', 'new.txt', 'text/plain', {}, uuid4().hex))
    new = service.send(actor, sid, 'two', 'Что в самом последнем csv?')
    assert service.send(actor, sid, 'two', new['question']) == new
    with pytest.raises(CoreError, match='Command key'):
        service.send(actor, sid, 'two', 'другое')
    assert service.run_once()
    history = service.read(actor, sid)
    assert [t['state'] for t in history['turns']] == ['completed', 'completed']
    assert history['turns'][1]['sources'][0]['artifact_id'] == str(latest.artifact_id)
    assert latest.artifact_id != first.artifact_id
    assert artifacts.read(actor, first).content == 'id,extra\n1,Первый\n'.encode()
    assert 'Второй' in history['turns'][1]['sources'][0]['excerpt']
    assert 'Чужой' not in model.calls[-1][1]
    assert 'UNTRUSTED' in model.calls[-1][1]
    assert len(model.calls) == 2
    assert not service.run_once()
    assert PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine)).read(actor, sid) == history
    with pg_engine.connect() as connection:
        assert connection.execute(select(document_indexes)).all()


def test_source_snapshot_is_pinned_at_send_and_original_integrity_is_checked(chat):
    service, actor, artifacts, _, model, store = chat
    first = upload(artifacts, actor)
    sid = UUID(service.create(actor, 's')['session_id'])
    service.send(actor, sid, 'm', 'данные')
    upload(artifacts, actor, 'id,extra\n2,Позднее\n')
    assert service.run_once()
    assert service.read(actor, sid)['turns'][0]['sources'][0]['artifact_id'] == str(first.artifact_id)
    # A later question must read and verify the original, even if chunks are cached.
    latest = upload(artifacts, actor, 'id,extra\n3,Повреждено\n')
    metadata = artifacts.metadata(actor, latest)
    digest = metadata['object_ref'].split(':')[1]
    (store.root / digest[:2] / digest).write_bytes(b'tampered')
    service.send(actor, sid, 'bad', 'данные')
    assert service.run_once()
    turn = service.read(actor, sid)['turns'][-1]
    assert turn['state'] == 'completed'
    unavailable = next(source for source in turn['sources'] if source['artifact_id'] == str(latest.artifact_id))
    assert unavailable['status'] == 'metadata_only' and unavailable['unit'] == 'metadata'
    assert 'Повреждено' not in model.calls[-1][1]
    assert len(model.calls) == 2


def test_no_files_is_explicit_and_failure_does_not_expose_model_secret(chat):
    service, actor, _, _, model, _ = chat
    sid = UUID(service.create(actor, 's')['session_id'])
    service.send(actor, sid, 'm', 'Что в csv?')
    assert service.run_once()
    turn = service.read(actor, sid)['turns'][0]
    assert turn['state'] == 'completed' and turn['sources'] == []
    assert 'NO_FILES' in model.calls[0][1]
    def fail(*_):
        raise RuntimeError('secret-provider-token')
    model.generate = fail
    service.send(actor, sid, 'bad', 'Продолжи')
    assert service.run_once()
    turn = service.read(actor, sid)['turns'][-1]
    assert turn['state'] == 'failed' and 'secret' not in json.dumps(turn)


def test_scope_permissions_and_session_owner(chat):
    service, actor, _, _, _, _ = chat
    sid = UUID(service.create(actor, 's')['session_id'])
    for altered in [replace(actor, data_scope='foreign'), replace(actor, actor_id='other')]:
        with pytest.raises(CoreError) as error:
            service.read(altered, sid)
        assert error.value.code == 'NOT_FOUND'
    for permission in ['chat:read', 'artifact:read', 'dataset:read']:
        with pytest.raises(CoreError) as error:
            service.read(replace(actor, permissions=actor.permissions - {permission}), sid)
        assert error.value.code == 'FORBIDDEN'
    with pytest.raises(CoreError):
        service.send(replace(actor, permissions=actor.permissions - {'chat:write'}), sid, 'm', 'hi')


def test_concurrent_commands_do_not_duplicate_or_mix_generation(chat):
    service, actor, artifacts, _, model, _ = chat
    upload(artifacts, actor)
    sid = UUID(service.create(actor, 's')['session_id'])
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: service.send(actor, sid, 'same', 'one'), range(4)))
    assert len({r['turn_id'] for r in results}) == 1
    service.send(actor, sid, 'two', 'two')
    entered, release = Event(), Event()
    generate = model.generate
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return generate(*args)
    model.generate = blocked
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(service.run_once)
        assert entered.wait(5)
        assert service.run_once() is False
        release.set()
        assert running.result()
    assert service.run_once()
    assert len(model.calls) == 2
    assert 'one' in model.calls[-1][1]


def test_interrupted_running_never_repeats_unknown_model_call(chat, pg_engine):
    service, actor, _, repo, model, _ = chat
    sid = UUID(service.create(actor, 's')['session_id'])
    turn = service.send(actor, sid, 'm', 'hello')
    # A crashed process releases its transaction advisory lock but leaves running durable.
    with repo.claim() as claimed:
        assert str(claimed['turn_id']) == turn['turn_id']
    assert service.read(actor, sid)['turns'][0]['state'] == 'running'
    assert service.run_once()
    assert service.read(actor, sid)['turns'][0]['state'] == 'failed'
    assert not model.calls and not service.run_once()


def test_http_contract_permissions_validation_and_reload(chat, pg_engine, tmp_path):
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from http_client import ASGITestClient
    _, actor, _, _, _, _ = chat
    keys = {'a' * 32: actor, 'b' * 32: replace(actor, permissions=actor.permissions - {'chat:write'})}
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), tmp_path / 'objects', keys)
    headers = {'Authorization': 'Bearer ' + 'a' * 32}
    with ASGITestClient(create_app(settings)) as http:
        response = http.post('/api/v0.1/chat/sessions', headers=headers, json={'command_key': 's'})
        assert response.status_code == 200
        session = response.json()
        assert set(session) == {'session_id', 'created_at', 'turns'}
        path = '/api/v0.1/chat/sessions/' + session['session_id']
        sent = http.post(path + '/messages', headers=headers, json={'command_key': 'm', 'content': 'Привет'})
        assert sent.status_code == 202
        assert set(sent.json()) == {'turn_id', 'question', 'answer', 'state', 'created_at', 'sources', 'error', 'results', 'actions', 'workflow'}
        assert sent.json()['results'] == sent.json()['actions'] == []
        assert http.post(path + '/messages', headers=headers, json={'command_key': 'empty', 'content': ' '}).status_code == 422
        assert http.post('/api/v0.1/chat/sessions', headers={'Authorization': 'Bearer ' + 'b' * 32}, json={'command_key': 'x'}).status_code == 403
    with ASGITestClient(create_app(settings)) as reopened:
        assert reopened.get(path, headers=headers).json()['turns'][0]['question'] == 'Привет'


def test_runner_stdlib_http_contract_and_safe_error():
    from bervel.core.infrastructure.codex import CodexClient
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'answer': 'Привет', 'model': 'fixture'}).encode())
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = CodexClient(f'http://127.0.0.1:{server.server_port}').generate('request', 'вопрос')
        assert result == {'answer': 'Привет', 'model': 'fixture'}
        assert calls == [('/v1/generate', {'request_id': 'request', 'prompt': 'вопрос'})]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_context_is_reverified_even_after_index_is_cached(chat):
    service, actor, artifacts, _, model, store = chat
    ref = upload(artifacts, actor)
    sid = UUID(service.create(actor, 's')['session_id'])
    service.send(actor, sid, 'first', 'Данные')
    service.run_once()
    sha = artifacts.metadata(actor, ref)['sha256']
    (store.root / sha[:2] / sha).write_bytes(b'broken')
    service.send(actor, sid, 'second', 'Данные')
    service.run_once()
    turn = service.read(actor, sid)['turns'][-1]
    assert turn['state'] == 'completed'
    assert turn['sources'][0]['status'] == 'metadata_only'
    assert 'Первый' not in json.loads(model.calls[-1][1].split('\n', 1)[1])['CURRENT_DOCUMENTS'][0]['excerpt']
    assert len(model.calls) == 2


def test_new_bad_csv_does_not_fall_back_to_an_older_dataset(chat):
    service, actor, artifacts, _, model, _ = chat
    upload(artifacts, actor)
    sid = UUID(service.create(actor, 's')['session_id'])
    artifacts.register(actor, RegisterArtifact(b'\xff\xfe', 'invalid.csv', 'text/csv', {}, uuid4().hex))
    service.send(actor, sid, 'm', 'Что в последнем csv?')
    service.run_once()
    turn = service.read(actor, sid)['turns'][0]
    assert turn['state'] == 'completed'
    assert all(source['filename'] == 'invalid.csv' and source['status'] == 'metadata_only' for source in turn['sources'])
    assert 'Первый' not in model.calls[0][1]


def test_filename_csv_and_timestamp_tie_have_deterministic_selection(chat, pg_engine):
    from bervel.core.infrastructure.database import artifacts as artifact_table
    service, actor, artifacts, _, _, _ = chat
    refs = [artifacts.register(actor, RegisterArtifact(f'id,name\n{n},Запись\n'.encode(), 'same.CSV',
            'application/octet-stream', {}, uuid4().hex)) for n in range(2)]
    timestamp = '2026-10-22T00:00:00+00:00'
    with pg_engine.begin() as connection:
        for ref in refs:
            meta = artifacts.metadata(actor, ref)
            connection.execute(update(artifact_table).where(artifact_table.c.artifact_id == ref.artifact_id)
                               .values(metadata={**meta, 'created_at': timestamp}))
    sid = UUID(service.create(actor, 's')['session_id'])
    service.send(actor, sid, 'm', 'последний')
    service.run_once()
    source = service.read(actor, sid)['turns'][0]['sources'][0]
    assert source['artifact_id'] == str(max(ref.artifact_id for ref in refs))
    assert set(source) == {'artifact_id', 'filename', 'created_at', 'row_start', 'row_end', 'total_rows', 'excerpt', 'truncated', 'unit', 'status', 'media_type'}


def test_bounded_prompt_does_not_rely_on_a_prior_answer():
    from bervel.modules.chat.service import build_prompt
    history = [{'question': 'old', 'answer': 'q' * 32000, 'state': 'completed'}] * 30
    prompt = build_prompt('now', history, [])
    assert len(prompt.encode()) < 256 * 1024
    assert 'NO_FILES' in prompt and 'obsolete' in prompt


def test_codex_environment_and_no_implicit_permission_upgrade(tmp_path, monkeypatch):
    from bervel.core.config import Settings
    monkeypatch.setenv('BERVEL_DATABASE_URL', 'postgresql+psycopg://app@postgres/app')
    monkeypatch.setenv('BERVEL_OBJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('BERVEL_ACCESS_KEYS', json.dumps([{'key': 'a' * 32, 'actor_id': 'actor', 'data_scope': 'a',
        'permissions': ['chat:read', 'artifact:read', 'dataset:read']}]))
    monkeypatch.setenv('BERVEL_CODEX_URL', 'http://runner:8090')
    settings = Settings.from_environment()
    assert settings.codex_url == 'http://runner:8090'
    assert 'chat:write' not in settings.access_keys['a' * 32].permissions


@pytest.mark.parametrize('status,payload', [
    (502, {'error': {'message': 'secret-provider-token'}}),
    (200, {'answer': '', 'model': 'm'}),
    (200, {'answer': 'x' * 32001, 'model': 'm'}),
    (200, {'answer': 'ok', 'model': None}),
    (200, ['invalid']),
])
def test_runner_failures_have_a_safe_domain_error(status, payload):
    from bervel.core.infrastructure.codex import CodexClient
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(CoreError) as error:
            CodexClient(f'http://127.0.0.1:{server.server_port}').generate('request', 'question')
        assert error.value.code == ('MODEL_UNAVAILABLE' if status != 200 else 'MODEL_INVALID_OUTPUT')
        assert 'secret-provider-token' not in str(error.value)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_two_sessions_may_generate_independently(chat):
    service, actor, _, _, model, _ = chat
    sessions = [UUID(service.create(actor, str(n))['session_id']) for n in range(2)]
    for sid in sessions:
        service.send(actor, sid, str(sid), 'question')
    entered, release = Event(), Event()
    generate = model.generate
    first = True
    def blocked(*args):
        nonlocal first
        if first:
            first = False
            entered.set()
            assert release.wait(5)
        return generate(*args)
    model.generate = blocked
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(service.run_once)
        assert entered.wait(5)
        assert service.run_once()
        release.set()
        assert running.result()
    assert all(service.read(actor, sid)['turns'][0]['state'] == 'completed' for sid in sessions)


def test_worker_model_failure_before_planning_does_not_claim_unread_csv_sources(chat, pg_engine, monkeypatch):
    from bervel.core.config import Settings
    from bervel.core.infrastructure.codex import CodexClient
    from bervel.worker import run_worker
    service, actor, artifacts, _, _, store = chat
    latest = upload(artifacts, actor, 'id,extra\n9,Новый ещё не извлечённый\n')
    sid = UUID(service.create(actor, 's')['session_id'])
    service.send(actor, sid, 'm', 'последний csv')
    def unavailable(*_):
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'model unavailable')
    monkeypatch.setattr(CodexClient, 'generate', unavailable)
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), store.root, {'a' * 32: actor})
    run_worker(settings,role='chat',once=True)
    turn = service.read(actor, sid)['turns'][0]
    assert turn['state'] == 'failed'
    assert turn['sources'] == [] and turn['results'] == []
    assert 'Новый' in artifacts.read(actor,latest).content.decode()


@pytest.mark.parametrize('code', ['MODEL_UNCONFIGURED', 'MODEL_TLS_ERROR', 'MODEL_AUTH_ERROR', 'MODEL_TIMEOUT', 'MODEL_BUSY'])
def test_expected_runner_error_is_classified_without_copying_upstream_text(code, chat):
    from bervel.core.infrastructure.codex import CodexClient
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(503)
            self.end_headers()
            self.wfile.write(json.dumps({'error': {'code': code, 'message': 'secret-provider-token'}}).encode())
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(CoreError) as failure:
            CodexClient(f'http://127.0.0.1:{server.server_port}').generate('request', 'question')
        assert failure.value.code == code
        service, actor, _, _, model, _ = chat
        sid = UUID(service.create(actor, 'expected-failure')['session_id'])
        service.send(actor, sid, 'question', 'Привет')
        def failed(*_):
            raise CoreError(code, 'secret-provider-token')
        model.generate = failed
        service.run_once()
        result = service.read(actor, sid)['turns'][0]
        assert result['state'] == 'failed'
        assert 'secret-provider-token' not in result['error']
        assert result['error'] != 'Не удалось получить ответ. Проверьте доступность модели и целостность CSV; повтор создаётся новым сообщением.'
    finally:
        server.shutdown(); server.server_close(); thread.join()
