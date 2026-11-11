"""Durable inspection inbox: ordering, receipts, cancellation and ownership."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import select

from bervel.core.errors import CoreError
from bervel.core.infrastructure.documents import PostgresDocumentRepository
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from bervel.modules.chat.infrastructure.tables import chat_turns
from test_inspection_native import ACTOR


@pytest.fixture
def inbox(pg_engine):
    repo = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine), mode='inspection')
    sid = UUID(repo.create(ACTOR, 'create')['session_id'])
    parent = repo.enqueue(ACTOR, sid, 'parent', 'Read the source')
    return repo, sid, UUID(parent['turn_id'])


def test_followups_attach_to_oldest_root_in_order_and_repeat_same_identity(inbox):
    repo, sid, parent = inbox
    first = repo.enqueue(ACTOR, sid, 'first', '  exact\nPASSWORD=raw\n ')
    second = repo.enqueue(ACTOR, sid, 'second', 'Also inspect tests')
    assert first['followup_of'] == str(parent) and first['delivery_state'] == 'pending'
    assert second['followup_of'] == str(parent)
    assert repo.enqueue(ACTOR, sid, 'first', first['question'])['turn_id'] == first['turn_id']
    assert [str(row['turn_id']) for row in repo.pending_followups(ACTOR, sid, parent)] == [first['turn_id'], second['turn_id']]
    with pytest.raises(CoreError) as conflict:
        repo.enqueue(ACTOR, sid, 'first', 'different')
    assert conflict.value.code == 'IDEMPOTENCY_CONFLICT'


def test_claim_skips_children_and_completion_releases_only_untouched_messages(inbox):
    repo, sid, parent = inbox
    first = repo.enqueue(ACTOR, sid, 'first', 'Uncertain delivery')
    second = repo.enqueue(ACTOR, sid, 'second', 'Untouched')
    with repo.claim() as claimed:
        assert claimed['turn_id'] == parent
        assert repo.mark_followup_sending(ACTOR, sid, parent, UUID(first['turn_id']))
        repo.finish(parent, answer='Done')
    values = repo.read(ACTOR, sid)['turns']
    assert values[1]['state'] == 'failed' and values[1]['delivery_state'] == 'unknown'
    assert values[2]['state'] == 'queued' and values[2]['followup_of'] is None
    with repo.claim() as claimed:
        assert str(claimed['turn_id']) == second['turn_id']
        repo.finish(claimed['turn_id'], answer='Next')
    with repo.claim() as claimed:
        assert claimed is None


def test_accepted_receipt_is_user_only_and_session_deletes_with_self_reference(inbox):
    repo, sid, parent = inbox
    child = UUID(repo.enqueue(ACTOR, sid, 'child', 'A clarification')['turn_id'])
    with repo.claim():
        repo.mark_followup_sending(ACTOR, sid, parent, child)
        repo.record_followup_delivery(ACTOR, sid, parent, child, 'accepted')
        repo.record_followup_delivery(ACTOR, sid, parent, child, 'accepted')
        repo.finish(parent, answer='Combined answer')
    value = repo.read(ACTOR, sid)['turns'][1]
    assert value['state'] == 'completed' and value['answer'] is None and value['delivery_state'] == 'accepted'
    assert repo.delete(ACTOR, sid, 'delete')['deleted']


@pytest.mark.parametrize('receipt', ['rejected', 'unknown'])
def test_rejected_detaches_but_unknown_never_replays(inbox, receipt):
    repo, sid, parent = inbox
    child = UUID(repo.enqueue(ACTOR, sid, 'child', 'A clarification')['turn_id'])
    with repo.claim():
        repo.mark_followup_sending(ACTOR, sid, parent, child)
        repo.record_followup_delivery(ACTOR, sid, parent, child, receipt)
        repo.finish(parent, answer='Done')
    value = repo.read(ACTOR, sid)['turns'][1]
    assert (value['state'], value['followup_of'], value['delivery_state']) == (
        ('queued', None, None) if receipt == 'rejected' else ('failed', str(parent), 'unknown'))


def test_stop_blocks_pending_and_rejected_children_but_keeps_accepted(inbox):
    repo, sid, parent = inbox
    ids = [UUID(repo.enqueue(ACTOR, sid, key, key)['turn_id']) for key in ('accepted', 'sending', 'pending')]
    with repo.claim():
        for child in ids[:2]:
            repo.mark_followup_sending(ACTOR, sid, parent, child)
        repo.record_followup_delivery(ACTOR, sid, parent, ids[0], 'accepted')
        repo.cancel(ACTOR, sid, parent, 'stop')
        assert not repo.mark_followup_sending(ACTOR, sid, parent, ids[2])
        repo.record_followup_delivery(ACTOR, sid, parent, ids[1], 'rejected')
        repo.finish(parent, outcome='blocked')
    values = repo.read(ACTOR, sid)['turns']
    assert [row['state'] for row in values[1:]] == ['completed', 'blocked', 'blocked']
    with repo.claim() as claimed:
        assert claimed is None


def test_inbox_access_checks_actor_scope_and_session(inbox):
    repo, sid, parent = inbox
    child = UUID(repo.enqueue(ACTOR, sid, 'child', 'Private')['turn_id'])
    other_sid = UUID(repo.create(ACTOR, 'other')['session_id'])
    for actor, session in [(replace(ACTOR, actor_id='other'), sid), (replace(ACTOR, data_scope='other'), sid), (ACTOR, other_sid)]:
        for operation in [lambda: repo.pending_followups(actor, session, parent),
                          lambda: repo.mark_followup_sending(actor, session, parent, child),
                          lambda: repo.record_followup_delivery(actor, session, parent, child, 'accepted')]:
            with pytest.raises(CoreError): operation()


def test_assistant_retains_independent_queue_and_dto(pg_engine):
    repo = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine))
    sid = UUID(repo.create(ACTOR, 'create')['session_id'])
    repo.enqueue(ACTOR, sid, 'first', 'First')
    second = repo.enqueue(ACTOR, sid, 'second', 'Second')
    assert 'followup_of' not in second and 'delivery_state' not in second
    with pg_engine.connect() as connection:
        assert connection.execute(select(chat_turns.c.followup_of).where(chat_turns.c.session_id == sid)).scalars().all() == [None, None]


def test_completion_racing_enqueue_never_strands_a_pending_child(inbox):
    repo, sid, parent = inbox
    gate = Barrier(2)
    def finish():
        gate.wait(timeout=5)
        repo.finish(parent, answer='Done')
    def enqueue():
        gate.wait(timeout=5)
        return repo.enqueue(ACTOR, sid, 'racing', 'Arrived at completion')
    with repo.claim():
        with ThreadPoolExecutor(max_workers=2) as pool:
            finishing = pool.submit(finish)
            added = pool.submit(enqueue).result(timeout=10)
            finishing.result(timeout=10)
    values = repo.read(ACTOR, sid)['turns']
    assert values[0]['state'] == 'completed'
    assert values[1]['state'] == 'queued' and values[1]['followup_of'] is None
    with repo.claim() as current:
        assert str(current['turn_id']) == added['turn_id']
        repo.finish(current['turn_id'], answer='Next')


def test_cancelling_a_queued_parent_blocks_its_inbox_without_starting_it(inbox):
    repo, sid, parent = inbox
    repo.enqueue(ACTOR, sid, 'child', 'Never start')
    repo.cancel(ACTOR, sid, parent, 'stop')
    assert [row['state'] for row in repo.read(ACTOR, sid)['turns']] == ['blocked', 'blocked']
    with repo.claim() as current:
        assert current is None


def test_existing_rows_survive_additive_migration_and_remain_root_turns(inbox, pg_engine):
    from alembic import command
    from alembic.config import Config
    from importlib.resources import files
    from bervel.core.infrastructure.database import migrate
    repo, sid, parent = inbox
    config = Config()
    config.set_main_option('script_location', str(files('bervel.core').joinpath('migrations')))
    with pg_engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, '0027_radar_legacy_history')
    migrate(pg_engine)
    old = repo.read(ACTOR, sid)['turns'][0]
    assert old['question'] == 'Read the source' and old['followup_of'] is None and old['delivery_state'] is None
    with repo.claim() as current:
        assert current['turn_id'] == parent
        repo.finish(parent, answer='Preserved')
