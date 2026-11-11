"""Mode belongs to durable sessions, never a model request or a browser flag."""
from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy import select

from bervel.core.errors import CoreError
from bervel.core.infrastructure.documents import PostgresDocumentRepository
from bervel.core.models import ActorContext
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from bervel.modules.chat.infrastructure.tables import chat_sessions


@pytest.fixture
def modes(pg_engine):
    documents = PostgresDocumentRepository(pg_engine)
    actor = ActorContext('reviewer', 'shared', frozenset({'chat:read', 'chat:write', 'artifact:read', 'dataset:read'}))
    return (PostgresChatRepository(pg_engine, documents),
            PostgresChatRepository(pg_engine, documents, mode='inspection'), actor)


def test_same_actor_command_keys_keep_mode_lists_and_history_separate(modes, pg_engine):
    assistant, inspection, actor = modes
    normal = assistant.create(actor, 'same-key')
    review = inspection.create(actor, 'same-key')
    assert normal['session_id'] != review['session_id']
    assert assistant.create(actor, 'same-key') == normal
    assert inspection.create(actor, 'same-key') == review
    assert [s['session_id'] for s in assistant.list_sessions(actor, 50, 0)['sessions']] == [normal['session_id']]
    assert [s['session_id'] for s in inspection.list_sessions(actor, 50, 0)['sessions']] == [review['session_id']]
    with pg_engine.connect() as connection:
        assert set(connection.execute(select(chat_sessions.c.mode)).scalars()) == {'assistant', 'inspection'}


@pytest.mark.parametrize('operation', ['read', 'enqueue', 'delete'])
def test_cross_mode_id_and_replay_cannot_address_another_chat(modes, operation):
    assistant, inspection, actor = modes
    sid = UUID(assistant.create(actor, 'new')['session_id'])
    arguments = {'read': (), 'enqueue': ('send', 'read everything'), 'delete': ('delete',)}[operation]
    with pytest.raises(CoreError) as error:
        getattr(inspection, operation)(actor, sid, *arguments)
    assert error.value.code == 'NOT_FOUND'
    assert assistant.read(actor, sid)['session_id'] == str(sid)


def test_workers_only_claim_their_persisted_mode(modes):
    assistant, inspection, actor = modes
    normal_id = UUID(assistant.create(actor, 'normal')['session_id'])
    review_id = UUID(inspection.create(actor, 'review')['session_id'])
    assistant.enqueue(actor, normal_id, 'message', 'ordinary')
    inspection.enqueue(actor, review_id, 'message', 'inspection')
    with inspection.claim() as turn:
        assert turn['session_id'] == review_id
        assert turn['question'] == 'inspection'
        inspection.finish(turn['turn_id'], answer='read only')
    with inspection.claim() as turn:
        assert turn is None
    with assistant.claim() as turn:
        assert turn['session_id'] == normal_id
        assistant.finish(turn['turn_id'], answer='ordinary')


def test_delete_receipts_cannot_replay_in_another_mode(modes):
    assistant, inspection, actor = modes
    sid = UUID(assistant.create(actor, 'normal')['session_id'])
    assert assistant.delete(actor, sid, 'remove')['deleted']
    with pytest.raises(CoreError) as error:
        inspection.delete(actor, sid, 'remove')
    assert error.value.code == 'NOT_FOUND'


def test_invalid_mode_is_rejected_before_database_use(pg_engine):
    with pytest.raises(ValueError):
        PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine), mode='root')
