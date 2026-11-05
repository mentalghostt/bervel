"""Session summaries and deletion respect ownership, durable work and created outputs."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, current_thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, insert, select, text, update

from bervel.core.errors import CoreError
from bervel.modules.chat.infrastructure.tables import chat_actions, chat_sessions, chat_turns
from test_chat import chat, upload


def sid(service, actor, key):
    return UUID(service.create(actor, key)['session_id'])


def assert_error(code, call):
    with pytest.raises(CoreError) as raised:
        call()
    assert raised.value.code == code


def rows(engine, table):
    with engine.connect() as connection:
        return connection.execute(text(f'SELECT row_to_json(t)::text FROM {table} t ORDER BY row_to_json(t)::text')).scalars().all()


def test_summary_scope_order_first_question_metadata_and_paging(chat, pg_engine):
    service, actor, _, _, _, _ = chat
    empty = sid(service, actor, 'empty')
    first = sid(service, actor, 'first')
    second = sid(service, actor, 'second')
    sid(service, replace(actor, actor_id='other'), 'other-actor')
    sid(service, replace(actor, data_scope='other'), 'other-scope')
    service.send(actor, first, 'first-message', ' \n\t' + 'Я' * 125 + '\n ')
    service.run_once()
    service.send(actor, first, 'second-message', 'Не заголовок')
    service.send(actor, second, 'pending-message', 'Ожидающий вопрос')
    same_time = datetime(2026, 10, 28, tzinfo=timezone.utc)
    with pg_engine.begin() as connection:
        connection.execute(update(chat_sessions).values(created_at=same_time))
    expected = sorted([empty, first, second], reverse=True)
    statements = []
    def capture(_connection, _cursor, statement, *_):
        statements.append(statement)
    event.listen(pg_engine, 'before_cursor_execute', capture)
    try:
        first_page = service.list_sessions(actor, limit=2, offset=0)
        last_page = service.list_sessions(actor, limit=2, offset=2)
    finally:
        event.remove(pg_engine, 'before_cursor_execute', capture)
    assert [entry['session_id'] for entry in first_page['sessions'] + last_page['sessions']] == list(map(str, expected))
    assert first_page['next_offset'] == 2 and last_page['next_offset'] is None
    summaries = {entry['session_id']: entry for entry in first_page['sessions'] + last_page['sessions']}
    assert summaries[str(empty)] == dict(session_id=str(empty), created_at=same_time.isoformat(), title='Новый диалог', turn_count=0, has_pending=False)
    assert summaries[str(first)]['title'] == 'Я' * 120
    assert summaries[str(first)]['turn_count'] == 2 and summaries[str(first)]['has_pending'] is True
    assert summaries[str(second)]['turn_count'] == 1 and summaries[str(second)]['has_pending'] is True
    assert service.list_sessions(actor, limit=3, offset=0)['next_offset'] is None
    assert service.list_sessions(actor, limit=2, offset=20) == {'sessions': [], 'next_offset': None}
    assert not any(word in statement for statement in statements for word in ('chat_actions', 'snapshot', 'sources', 'answer', 'results'))
    # Creation time, rather than activity, controls the primary order.
    with pg_engine.begin() as connection:
        connection.execute(update(chat_sessions).where(chat_sessions.c.session_id == empty).values(created_at=datetime(2026, 10, 29, tzinfo=timezone.utc)))
    assert service.list_sessions(actor)['sessions'][0]['session_id'] == str(empty)


def test_delete_removes_correspondence_and_action_context_but_preserves_created_outputs(chat, services, pg_engine):
    from test_assistant_workflows import build_actions, content
    from test_knowledge import setup
    service, actor, artifacts, _, _, store = chat
    session = sid(service, actor, 's')
    other = sid(service, actor, 'other')
    source = upload(artifacts, actor)
    turn = service.send(actor, session, 'm', 'Секретный вопрос')
    assert service.run_once()
    service.send(actor, other, 'other-message', 'Другой диалог')
    actions = build_actions(services, pg_engine)
    file_result = actions.execute(actor, UUID(turn['turn_id']), 0, 'file.create',
        {'format': 'txt', 'filename': 'generated.txt', 'title': 'Документ', 'content': 'Созданный результат'})
    ticket_result = actions.execute(actor, UUID(turn['turn_id']), 1, 'ticket.create', content())
    knowledge, *_ = setup(services, pg_engine)
    while knowledge.run_once():
        pass
    with pg_engine.begin() as connection:
        connection.execute(update(chat_turns).where(chat_turns.c.turn_id == UUID(turn['turn_id'])).values(results=[file_result, ticket_result]))
        # A foreign action record must not be removed even if it points to the same turn.
        connection.execute(insert(chat_actions).values(action_id=uuid4(), turn_id=UUID(turn['turn_id']), ordinal=0,
            actor_id='foreign', data_scope=actor.data_scope, operation='file.read', arguments={'context': 'foreign'},
            fingerprint='0' * 64, state='completed', result={}, created_at=datetime.now(timezone.utc)))
        connection.execute(text("""INSERT INTO ai_tender_radar_jobs
            (job_id,data_scope,actor_id,command_key,stock_artifact_id,stock_sha256,search_phrase,state,analysis)
            VALUES(:id,:scope,:actor,'preserved-ai-job',:artifact,repeat('a',64),'болт','loaded','{"result":"preserved"}')"""),
            dict(id=uuid4(), scope=actor.data_scope, actor=actor.actor_id, artifact=source.artifact_id))
    with pg_engine.connect() as connection:
        tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND tablename NOT IN ('chat_sessions','chat_turns','chat_actions','audit','command_receipts')")).scalars().all()
    preserved = {table: rows(pg_engine, table) for table in tables}
    before_other = service.read(actor, other)
    before_audit = rows(pg_engine, 'audit')
    before_receipts = rows(pg_engine, 'command_receipts')
    before_objects = {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    assert service.delete(actor, session, 'delete-s') == {'session_id': str(session), 'deleted': True}
    assert_error('NOT_FOUND', lambda: service.read(actor, session))
    assert service.read(actor, other) == before_other
    assert {table: rows(pg_engine, table) for table in tables} == preserved
    assert set(before_audit) < set(rows(pg_engine, 'audit'))
    assert set(before_receipts) < set(rows(pg_engine, 'command_receipts'))
    assert {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before_objects
    with pg_engine.connect() as connection:
        assert connection.execute(select(chat_turns).where(chat_turns.c.session_id == session)).all() == []
        remaining = connection.execute(select(chat_actions).where(chat_actions.c.turn_id == UUID(turn['turn_id']))).mappings().all()
        assert len(remaining) == 1 and remaining[0]['actor_id'] == 'foreign'


def test_delete_receipt_replay_conflicting_input_and_old_commands_never_resurrect(chat, pg_engine):
    service, actor, _, _, model, _ = chat
    session = sid(service, actor, 'original-create')
    other = sid(service, actor, 'other')
    service.send(actor, session, 'original-send', 'original')
    service.run_once()
    result = service.delete(actor, session, 'delete')
    audit = rows(pg_engine, 'audit')
    assert service.delete(actor, session, 'delete') == result
    assert rows(pg_engine, 'audit') == audit
    assert_error('IDEMPOTENCY_CONFLICT', lambda: service.delete(actor, other, 'delete'))
    assert_error('NOT_FOUND', lambda: service.delete(actor, session, 'new-delete'))
    assert_error('NOT_FOUND', lambda: service.create(actor, 'original-create'))
    assert_error('NOT_FOUND', lambda: service.send(actor, session, 'original-send', 'original'))
    assert_error('NOT_FOUND', lambda: service.send(actor, session, 'new-send', 'new'))
    assert len(model.calls) == 1 and service.run_once() is False
    assert [row['session_id'] for row in service.list_sessions(actor)['sessions']] == [str(other)]


@pytest.mark.parametrize('state', ['queued', 'running'])
def test_delete_rejects_durable_pending_work_without_changes(chat, pg_engine, state):
    service, actor, _, _, _, _ = chat
    session = sid(service, actor, 's')
    service.send(actor, session, 'm', 'work')
    with pg_engine.begin() as connection:
        connection.execute(update(chat_turns).values(state=state))
    assert service.list_sessions(actor)['sessions'][0]['has_pending'] is True
    before = {table: rows(pg_engine, table) for table in ('chat_sessions', 'chat_turns', 'chat_actions', 'command_receipts', 'audit')}
    assert_error('CONFLICT', lambda: service.delete(actor, session, 'delete'))
    assert {table: rows(pg_engine, table) for table in before} == before


def test_delete_rejects_actual_worker_ownership_even_after_turn_finishes(chat):
    service, actor, _, repository, _, _ = chat
    session = sid(service, actor, 's')
    service.send(actor, session, 'm', 'work')
    with repository.claim() as claimed:
        repository.finish(claimed['turn_id'], answer='done')
        assert service.list_sessions(actor)['sessions'][0]['has_pending'] is False
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(service.delete, actor, session, 'delete')
            with pytest.raises(CoreError) as raised:
                future.result(timeout=3)
            assert raised.value.code == 'CONFLICT'
    assert service.delete(actor, session, 'delete')['deleted'] is True


def test_running_worker_can_finish_after_rejected_delete(chat):
    service, actor, _, _, model, _ = chat
    session = sid(service, actor, 's')
    service.send(actor, session, 'm', 'work')
    entered, release = Event(), Event()
    original = model.generate
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    model.generate = blocked
    with ThreadPoolExecutor(1) as pool:
        worker = pool.submit(service.run_once)
        try:
            assert entered.wait(5)
            assert_error('CONFLICT', lambda: service.delete(actor, session, 'delete'))
        finally:
            release.set()
        assert worker.result(timeout=5) is True
    assert service.read(actor, session)['turns'][0]['state'] == 'completed'
    assert service.delete(actor, session, 'delete')['deleted'] is True


def test_enqueue_rechecks_session_after_delete_wins_stale_read(chat, monkeypatch):
    service, actor, _, repository, _, _ = chat
    session = sid(service, actor, 's')
    read, release = Event(), Event()
    original = repository._session
    def gate(connection, context, session_id):
        result = original(connection, context, session_id)
        if current_thread().name.startswith('stale-send') and not read.is_set():
            read.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(repository, '_session', gate)
    with ThreadPoolExecutor(1, thread_name_prefix='stale-send') as pool:
        pending = pool.submit(service.send, actor, session, 'late-send', 'late')
        try:
            assert read.wait(5)
            assert service.delete(actor, session, 'delete')['deleted'] is True
        finally:
            release.set()
        with pytest.raises(CoreError) as raised:
            pending.result(timeout=5)
        assert raised.value.code == 'NOT_FOUND'
    assert service.list_sessions(actor)['sessions'] == []


def test_delete_waits_for_inflight_enqueue_and_then_rejects_pending_work(chat, monkeypatch):
    service, actor, _, repository, _, _ = chat
    session = sid(service, actor, 's')
    enqueuing, deleting, release = Event(), Event(), Event()
    original = repository.documents.catalog
    def blocked_catalog(*args):
        result = original(*args)
        enqueuing.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(repository.documents, 'catalog', blocked_catalog)
    def delete_after_enqueue():
        deleting.set()
        return service.delete(actor, session, 'delete')
    with ThreadPoolExecutor(2) as pool:
        send = pool.submit(service.send, actor, session, 'm', 'work')
        try:
            assert enqueuing.wait(5)
            deletion = pool.submit(delete_after_enqueue)
            assert deleting.wait(5)
            with pytest.raises(TimeoutError):
                deletion.result(timeout=0.2)
        finally:
            release.set()
        queued = send.result(timeout=5)
        with pytest.raises(CoreError) as raised:
            deletion.result(timeout=5)
        assert raised.value.code == 'CONFLICT'
    assert service.read(actor, session)['turns'][0]['turn_id'] == queued['turn_id']


@pytest.mark.parametrize('alteration', ['actor', 'scope'])
def test_foreign_delete_has_no_side_effects(chat, pg_engine, alteration):
    service, actor, _, _, _, _ = chat
    session = sid(service, actor, 's')
    foreign = replace(actor, **({'actor_id': 'foreign'} if alteration == 'actor' else {'data_scope': 'foreign'}))
    before = {table: rows(pg_engine, table) for table in ('chat_sessions', 'command_receipts', 'audit')}
    assert_error('NOT_FOUND', lambda: service.delete(foreign, session, 'delete'))
    assert {table: rows(pg_engine, table) for table in before} == before


@pytest.mark.parametrize('permission', ['chat:read', 'artifact:read', 'dataset:read'])
def test_summary_permissions(chat, permission):
    service, actor, *_ = chat
    assert_error('FORBIDDEN', lambda: service.list_sessions(replace(actor, permissions=actor.permissions - {permission})))


@pytest.mark.parametrize('permission', ['chat:write', 'artifact:read', 'dataset:read'])
def test_delete_permissions(chat, permission):
    service, actor, *_ = chat
    session = sid(service, actor, 's')
    assert_error('FORBIDDEN', lambda: service.delete(replace(actor, permissions=actor.permissions - {permission}), session, 'delete'))


@pytest.mark.parametrize('key', ['', ' ', 'x' * 129])
def test_delete_key_validation(chat, key):
    service, actor, *_ = chat
    session = sid(service, actor, 's')
    assert_error('VALIDATION_ERROR', lambda: service.delete(actor, session, key))


@pytest.mark.parametrize('limit,offset', [(0, 0), (101, 0), (1, -1)])
def test_summary_pagination_validation(chat, limit, offset):
    service, actor, *_ = chat
    assert_error('VALIDATION_ERROR', lambda: service.list_sessions(actor, limit=limit, offset=offset))


def test_http_summary_delete_contract_permissions_validation_and_conflict(chat, pg_engine, tmp_path):
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from http_client import ASGITestClient
    service, actor, *_ = chat
    keys = {'a' * 32: actor, 'b' * 32: replace(actor, data_scope='foreign'),
        'c' * 32: replace(actor, permissions=actor.permissions - {'chat:write'}),
        'd' * 32: replace(actor, permissions=actor.permissions - {'chat:read'})}
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), tmp_path / 'objects', keys)
    headers = lambda key: {'Authorization': 'Bearer ' + key * 32}
    prefix = '/api/v0.1/chat/sessions'
    session = sid(service, actor, 's')
    path = prefix + '/' + str(session)
    with ASGITestClient(create_app(settings)) as http:
        assert http.get(prefix).status_code == 401
        assert http.get(prefix, headers=headers('d')).status_code == 403
        assert http.get(prefix, headers=headers('b')).json() == {'sessions': [], 'next_offset': None}
        listing = http.get(prefix, headers=headers('a'))
        assert listing.status_code == 200
        assert set(listing.json()) == {'sessions', 'next_offset'}
        summary = listing.json()['sessions'][0]
        assert set(summary) == {'session_id', 'created_at', 'title', 'turn_count', 'has_pending'}
        assert summary['title'] == 'Новый диалог' and summary['has_pending'] is False
        for query in ('limit=0', 'limit=101', 'offset=-1', 'limit=nope'):
            assert http.get(prefix + '?' + query, headers=headers('a')).status_code == 422
        assert http.delete(path + '?command_key=delete').status_code == 401
        assert http.delete(path + '?command_key=delete', headers=headers('c')).status_code == 403
        assert http.delete(path + '?command_key=delete', headers=headers('b')).status_code == 404
        for suffix in ('', '?command_key=', '?command_key=%20', '?command_key=' + 'x' * 129):
            assert http.delete(path + suffix, headers=headers('a')).status_code == 422
        service.send(actor, session, 'm', 'work')
        conflict = http.delete(path + '?command_key=delete', headers=headers('a'))
        assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'CONFLICT'
        assert http.delete(prefix + '/invalid?command_key=delete', headers=headers('a')).status_code == 422
        assert service.run_once()
        result = http.delete(path + '?command_key=delete', headers=headers('a'))
        assert result.status_code == 200 and result.json() == {'session_id': str(session), 'deleted': True}
        assert http.delete(path + '?command_key=delete', headers=headers('a')).json() == result.json()
        assert http.get(path, headers=headers('a')).status_code == 404
        assert http.post(prefix, headers=headers('a'), json={'command_key': 's'}).status_code == 404
        assert http.post(path + '/messages', headers=headers('a'), json={'command_key': 'm', 'content': 'work'}).status_code == 404
