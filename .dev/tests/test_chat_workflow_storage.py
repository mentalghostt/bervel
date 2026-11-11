"""Durable phase checkpoints, public summaries and additive legacy migration."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from importlib.resources import files
from inspect import signature
import json
import os
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, select, text

from bervel.core.errors import CoreError
from bervel.core.infrastructure.database import migrate
from bervel.core.infrastructure.documents import PostgresDocumentRepository
from bervel.modules.chat.api import TurnResponse
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository, turn_view
from bervel.modules.chat.infrastructure.tables import chat_turns


PUBLIC_FIELDS = {'phase', 'updated_at', 'reason', 'research_rounds', 'max_research_rounds',
                 'web_searches', 'max_web_searches', 'elapsed_seconds', 'max_research_seconds', 'history'}


@pytest.fixture
def storage(services, pg_engine):
    *_, actor = services
    actor = replace(actor, permissions=actor.permissions | {'chat:read', 'chat:write'})
    return PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine)), actor


def enqueue(storage, key='message'):
    repository, actor = storage
    session_id = UUID(repository.create(actor, 'session-' + key)['session_id'])
    return session_id, repository.enqueue(actor, session_id, key, 'Подготовь документ')


def stored(engine, turn_id):
    with engine.connect() as connection:
        return dict(connection.execute(select(chat_turns).where(
            chat_turns.c.turn_id == UUID(str(turn_id)))).mappings().one())


def advance(workflow, phase=None, reason=None):
    following = deepcopy(workflow)
    following['version'] += 1
    following['updated_at'] = datetime.now(timezone.utc).isoformat()
    if phase and phase != following['phase']:
        following['phase'] = phase
        following['reason'] = reason
        following['history'].append({'phase': phase, 'at': following['updated_at'], 'reason': reason})
    return following


def require_checkpoints(repository):
    assert callable(getattr(repository, 'checkpoint', None)), 'Chat repository must persist phase checkpoints'


def require_outcomes(repository):
    assert {'outcome', 'workflow'} <= signature(repository.finish).parameters.keys(), \
        'Completion must accept an explicit terminal outcome and final checkpoint'


def assert_stale(call):
    with pytest.raises(CoreError) as raised:
        call()
    assert raised.value.code == 'STALE_ATTEMPT'


def test_new_turn_initializes_workflow_without_changing_command_identity(storage, pg_engine):
    repository, actor = storage
    session_id, turn = enqueue(storage)
    assert 'workflow' in turn, 'New chat turns must expose their server phase'
    assert set(turn['workflow']) == PUBLIC_FIELDS
    assert turn['workflow']['phase'] == 'research'
    assert turn['workflow']['max_research_rounds'] == 4
    assert turn['workflow']['max_web_searches'] == 2
    assert turn['workflow']['max_research_seconds'] == 240
    saved = stored(pg_engine, turn['turn_id'])
    assert saved['workflow']['version'] == 0
    assert saved['workflow']['history'][0]['phase'] == 'research'
    assert repository.enqueue(actor, session_id, 'message', 'Подготовь документ') == turn
    assert stored(pg_engine, turn['turn_id'])['workflow'] == saved['workflow']
    with pytest.raises(CoreError, match='IDEMPOTENCY_CONFLICT'):
        repository.enqueue(actor, session_id, 'message', 'Другой документ')


def test_checkpoint_is_committed_for_polling_without_finishing_the_turn(storage, pg_engine):
    repository, actor = storage
    require_checkpoints(repository)
    session_id, turn = enqueue(storage)
    with repository.claim() as claimed:
        initial = deepcopy(claimed['workflow'])
        following = advance(initial, 'synthesizing')
        repository.checkpoint(claimed['turn_id'], following)
        fresh = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine))
        visible = fresh.read(actor, session_id)['turns'][0]
        assert visible['state'] == 'running' and visible['workflow']['phase'] == 'synthesizing'
        assert stored(pg_engine, turn['turn_id'])['workflow'] == following
        assert initial['version'] == 0 and following['version'] == 1
        assert set(visible['workflow']) == PUBLIC_FIELDS


def test_checkpoint_rejects_stale_versions_and_phase_rollback_without_mutation(storage, pg_engine):
    repository, _ = storage
    require_checkpoints(repository)
    _, turn = enqueue(storage)
    with repository.claim() as claimed:
        first = advance(claimed['workflow'], 'synthesizing')
        repository.checkpoint(claimed['turn_id'], first)
        second = advance(first, 'generating')
        repository.checkpoint(claimed['turn_id'], second)
        assert_stale(lambda: repository.checkpoint(claimed['turn_id'], first))
        rollback = advance(second, 'research')
        with pytest.raises(CoreError):
            repository.checkpoint(claimed['turn_id'], rollback)
        skipped = advance(second)
        skipped['version'] += 1
        assert_stale(lambda: repository.checkpoint(claimed['turn_id'], skipped))
        assert stored(pg_engine, turn['turn_id'])['workflow'] == second


def test_competing_checkpoints_allow_exactly_one_version_owner(storage, pg_engine):
    repository, _ = storage
    require_checkpoints(repository)
    _, turn = enqueue(storage)
    with repository.claim() as claimed:
        following = advance(claimed['workflow'], 'synthesizing')
        def checkpoint(_):
            try:
                repository.checkpoint(claimed['turn_id'], following)
                return 'saved'
            except CoreError as error:
                return error.code
        with ThreadPoolExecutor(2) as pool:
            assert sorted(pool.map(checkpoint, range(2))) == ['STALE_ATTEMPT', 'saved']
        assert stored(pg_engine, turn['turn_id'])['workflow'] == following


def test_checkpoint_refuses_queued_missing_and_finished_rows(storage, pg_engine):
    repository, _ = storage
    require_checkpoints(repository)
    _, turn = enqueue(storage)
    initial = stored(pg_engine, turn['turn_id'])['workflow']
    assert_stale(lambda: repository.checkpoint(UUID(turn['turn_id']), advance(initial)))
    assert_stale(lambda: repository.checkpoint(uuid4(), advance(initial)))
    with repository.claim() as claimed:
        repository.finish(claimed['turn_id'], answer='Ответ')
    before = stored(pg_engine, turn['turn_id'])
    assert_stale(lambda: repository.checkpoint(UUID(turn['turn_id']), advance(initial)))
    assert stored(pg_engine, turn['turn_id']) == before


@pytest.mark.parametrize('outcome', ['partial', 'blocked'])
def test_explicit_outcome_is_terminal_stable_scoped_and_not_requeued(storage, pg_engine, outcome):
    repository, actor = storage
    require_outcomes(repository)
    require_checkpoints(repository)
    session_id, turn = enqueue(storage)
    with repository.claim() as claimed:
        following = advance(claimed['workflow'], outcome, 'Недостаточно исходных данных')
        repository.finish(claimed['turn_id'], answer='Сохранённый результат', outcome=outcome, workflow=following)
    before = stored(pg_engine, turn['turn_id'])
    assert before['state'] == outcome and before['workflow'] == following
    assert repository.read(actor, session_id)['turns'][0]['workflow']['reason'] == 'Недостаточно исходных данных'
    assert repository.list_sessions(actor, 50, 0)['sessions'][0]['has_pending'] is False
    with repository.claim() as claimed:
        assert claimed is None
    assert_stale(lambda: repository.finish(UUID(turn['turn_id']), answer='Перезапись', outcome='completed'))
    assert_stale(lambda: repository.checkpoint(UUID(turn['turn_id']), advance(following)))
    assert stored(pg_engine, turn['turn_id']) == before
    for foreign in (replace(actor, actor_id='foreign'), replace(actor, data_scope='foreign')):
        with pytest.raises(CoreError, match='NOT_FOUND'):
            repository.read(foreign, session_id)


def test_finish_validates_outcome_and_workflow_together_atomically(storage, pg_engine):
    repository, _ = storage
    require_outcomes(repository)
    _, turn = enqueue(storage)
    with repository.claim() as claimed:
        before = stored(pg_engine, turn['turn_id'])
        for outcome, workflow in [('invalid', None), ('completed', advance(claimed['workflow'], 'partial'))]:
            with pytest.raises(CoreError):
                repository.finish(claimed['turn_id'], answer='Не сохранять', outcome=outcome, workflow=workflow)
            assert stored(pg_engine, turn['turn_id']) == before


def test_public_turn_projection_drops_internal_evidence_and_history_extras(storage, pg_engine):
    _, turn = enqueue(storage)
    row = stored(pg_engine, turn['turn_id'])
    assert isinstance(row.get('workflow'), dict), 'Workflow must be durable JSONB'
    row['workflow'].update(goal={'secret': 'goal'}, evidence={'secret': 'sources'}, draft='private draft')
    row['workflow']['history'][0]['private'] = 'internal history'
    public = turn_view(row)
    assert set(public['workflow']) == PUBLIC_FIELDS
    assert set(public['workflow']['history'][0]) == {'phase', 'at', 'reason'}
    assert not any(secret in json.dumps(public) for secret in ('private draft', 'internal history', 'secret'))


def test_dto_accepts_terminal_outcomes_and_nullable_legacy_workflow():
    assert 'workflow' in TurnResponse.model_fields, 'HTTP DTO must include the phase summary'
    legacy = dict(turn_id=uuid4(), question='Старый вопрос', answer='Ответ', state='completed',
                  created_at=datetime.now(timezone.utc), sources=[], error=None)
    assert TurnResponse.model_validate(legacy).model_dump()['workflow'] is None
    summary = dict(phase='partial', updated_at='2026-10-28T00:00:00+00:00', reason='Лимит', research_rounds=4,
                   max_research_rounds=4, web_searches=2, max_web_searches=2, elapsed_seconds=240,
                   max_research_seconds=240, history=[dict(phase='research', at='2026-10-28T00:00:00+00:00', reason=None)])
    for outcome in ('completed', 'partial', 'blocked'):
        response = TurnResponse.model_validate({**legacy, 'state': outcome, 'workflow': {**summary, 'draft': 'private'}})
        assert response.state == outcome
        assert set(response.model_dump(mode='json')['workflow']) == PUBLIC_FIELDS


def test_http_polling_reads_server_phase_after_reload_without_resending(storage, pg_engine, tmp_path):
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from http_client import ASGITestClient
    repository, actor = storage
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), tmp_path / 'objects', {'a' * 32: actor})
    headers = {'Authorization': 'Bearer ' + 'a' * 32}
    with ASGITestClient(create_app(settings)) as http:
        response = http.post('/api/v0.1/chat/sessions', headers=headers, json={'command_key': 'http-session'})
        path = '/api/v0.1/chat/sessions/' + response.json()['session_id']
        sent = http.post(path + '/messages', headers=headers, json={'command_key': 'http-message', 'content': 'Документ'})
        assert sent.status_code == 202
        assert sent.json().get('workflow', {}).get('phase') == 'research', 'POST must return the persisted phase'
    with repository.claim() as claimed:
        repository.checkpoint(claimed['turn_id'], advance(claimed['workflow'], 'synthesizing'))
        with ASGITestClient(create_app(settings)) as reloaded:
            response = reloaded.get(path, headers=headers)
            assert response.status_code == 200
            assert len(response.json()['turns']) == 1
            public = response.json()['turns'][0]['workflow']
            assert public['phase'] == 'synthesizing' and set(public) == PUBLIC_FIELDS


@pytest.fixture
def legacy_engine():
    admin = create_engine(os.environ['BERVEL_TEST_DATABASE_URL'])
    assert admin.url.database == 'bervel_core_stage31_test'
    schema = 't_' + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(admin.url.update_query_dict({'options': f'-csearch_path={schema}'}))
    config = Config()
    config.set_main_option('script_location', str(files('bervel.core').joinpath('migrations')))
    try:
        with engine.begin() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, '0013_files_without_revisions')
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


def test_upgrade_preserves_every_legacy_turn_field_and_does_not_fabricate_workflow(legacy_engine):
    session, turn = uuid4(), uuid4()
    with legacy_engine.begin() as connection:
        connection.execute(text("INSERT INTO chat_sessions VALUES(:id,'actor','scope',now())"), {'id': session})
        connection.execute(text("""INSERT INTO chat_turns(turn_id,session_id,ordinal,question,answer,state,
            snapshot,sources,actor_permissions,created_at,results) VALUES(:turn,:session,1,'Вопрос','Ответ','completed',
            '[{"filename":"source.txt"}]','[{"excerpt":"source"}]','["chat:read"]',now(),'[{"kind":"file"}]')"""),
            {'turn': turn, 'session': session})
        before = dict(connection.execute(text('SELECT * FROM chat_turns')).mappings().one())
    migrate(legacy_engine)
    assert 'workflow' in {c['name'] for c in inspect(legacy_engine).get_columns('chat_turns')}, 'Upgrade must add nullable workflow'
    with legacy_engine.connect() as connection:
        after = dict(connection.execute(text('SELECT * FROM chat_turns')).mappings().one())
        assert after.pop('workflow') is None
        assert after.pop('cancel_requested') is False
        assert after.pop('followup_of') is None
        assert after.pop('delivery_state') is None
        assert after == before
        assert turn_view({**after, 'workflow': None})['workflow'] is None
    migrate(legacy_engine)
    with legacy_engine.connect() as connection:
        assert connection.execute(text('SELECT workflow FROM chat_turns')).scalar_one() is None


def test_fresh_schema_and_table_metadata_agree_on_nullable_jsonb_and_terminal_states(pg_engine):
    columns = {c['name']: c for c in inspect(pg_engine).get_columns('chat_turns')}
    assert 'workflow' in columns, 'Fresh migrated database must persist workflow'
    assert columns['workflow']['nullable'] and str(columns['workflow']['type']) == 'JSONB'
    assert columns['workflow']['default'] is None
    assert 'workflow' in chat_turns.c and chat_turns.c.workflow.nullable
    constraints = ' '.join(c['sqltext'] for c in inspect(pg_engine).get_check_constraints('chat_turns'))
    metadata_constraints = ' '.join(str(c.sqltext) for c in chat_turns.constraints if hasattr(c, 'sqltext'))
    for state in ('queued', 'running', 'completed', 'partial', 'blocked', 'failed'):
        assert state in constraints and state in metadata_constraints
