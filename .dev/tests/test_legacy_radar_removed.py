"""Retiring the legacy radar must leave the independent AI radar usable and intact."""
from importlib.resources import files
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, text

from test_api import client, auth, PREFIX


def test_only_ai_radar_routes_remain(client):
    http, keys, _ = client
    paths = http.get('/openapi.json').json()['paths']
    assert not any(path.startswith(PREFIX + '/radar/') for path in paths)
    assert PREFIX + '/ai-tender-radar/cards' in paths
    assert http.get(PREFIX + '/radar/runs', headers=auth(keys[0])).status_code == 404
    assert http.get(PREFIX + '/ai-tender-radar/cards').status_code == 401
    assert http.get(PREFIX + '/ai-tender-radar/cards', headers=auth(keys[0])).status_code == 403


@pytest.mark.parametrize('operation,arguments', [
    ('radar.import', {'artifact_id': '00000000-0000-0000-0000-000000000001'}),
    ('radar.batches', {}), ('radar.list', {}),
    ('radar.read', {'run_id': '00000000-0000-0000-0000-000000000001'}),
    ('radar.pdf', {'run_id': '00000000-0000-0000-0000-000000000001'}),
    ('radar.read_page', {'run_id': '00000000-0000-0000-0000-000000000001'}),
    ('radar.analyse', {**{role: {'artifact_id': '00000000-0000-0000-0000-000000000001'}
        for role in ('procurement', 'stock', 'economics')}, 'filters': {}}),
])
def test_assistant_rejects_retired_operations(operation, arguments):
    from bervel.core.errors import CoreError
    from bervel.modules.chat.planner import validate_arguments
    with pytest.raises(CoreError) as rejected:
        validate_arguments(operation, arguments)
    assert rejected.value.code == 'ASSISTANT_INVALID_PLAN'


def test_retired_worker_cannot_connect_or_claim_work(monkeypatch):
    import bervel.worker as worker
    monkeypatch.setattr(worker, 'build_services', lambda *a, **kw: pytest.fail('Retired worker connected'))
    with pytest.raises(ValueError):
        worker.run_worker(SimpleNamespace(), role='radar', once=True)


def test_upgrade_deletes_legacy_history_and_preserves_ai_and_shared_data(pg_engine):
    schema = 'retire_' + uuid4().hex
    with pg_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA {schema}')
        connection.exec_driver_sql(f'SET LOCAL search_path TO {schema}')
        config = Config()
        config.set_main_option('script_location', str(files('bervel.core').joinpath('migrations')))
        config.attributes['connection'] = connection
        command.upgrade(config, '0023_ai_tender_radar_archive')
        connection.exec_driver_sql("""
            INSERT INTO artifacts (artifact_id, data_scope, owner_module_id, metadata, usage_role)
            VALUES ('00000000-0000-0000-0000-000000000001', 'shared', 'workspace', '{}', 'stock');
            INSERT INTO radar_batches (batch_id, data_scope, actor_id, artifact_id, filename, data, created_at)
            VALUES ('00000000-0000-0000-0000-000000000002', 'shared', 'owner',
                '00000000-0000-0000-0000-000000000001', 'stock.csv', '{}', now());
            INSERT INTO radar_runs (run_id, batch_id, actor_id, data_scope, actor_permissions,
                sources_snapshot, focus, state, created_at, report)
            VALUES ('00000000-0000-0000-0000-000000000003', '00000000-0000-0000-0000-000000000002',
                'owner', 'shared', '[]', '{}', '', 'completed', now(), '{"legacy":true}');
            INSERT INTO ai_tender_radar_jobs (job_id, data_scope, actor_id, command_key, stock_artifact_id,
                stock_sha256, search_phrase, state, stock, tender, snapshot, analysis)
            VALUES ('00000000-0000-0000-0000-000000000004', 'shared', 'owner', 'ai-keep',
                '00000000-0000-0000-0000-000000000001', repeat('a',64), 'болт', 'loaded',
                '{"keep":"stock"}', '{"keep":"tender"}', '{"keep":"snapshot"}', '{"keep":"analysis"}');
            INSERT INTO command_receipts (data_scope, actor_id, operation, command_key, fingerprint, result)
            VALUES ('shared', 'owner', 'radar.start', 'old', 'old', '{"run_id":"legacy"}'),
                ('shared', 'owner', 'artifact.register', 'keep', 'keep', '{"keep":true}');
            INSERT INTO audit (audit_id, data_scope, actor_id, operation, object_ref, created_at)
            VALUES ('00000000-0000-0000-0000-000000000005', 'shared', 'owner', 'radar.start', '{}', now()),
                ('00000000-0000-0000-0000-000000000006', 'shared', 'owner', 'artifact.register', '{}', now());
            INSERT INTO chat_sessions (session_id, actor_id, data_scope, created_at)
            VALUES ('00000000-0000-0000-0000-000000000007', 'owner', 'shared', now());
            INSERT INTO chat_turns (turn_id, session_id, ordinal, question, answer, state, sources,
                actor_permissions, results, created_at)
            VALUES ('00000000-0000-0000-0000-000000000008', '00000000-0000-0000-0000-000000000007',
                1, 'ИИ-радар сохраняется', 'Ответ сохраняется', 'completed', '[]', '[]',
                '[{"kind":"radar_run","run_id":"old"},{"kind":"data","operation":"radar.read","data":{}},{"kind":"file","artifact_id":"keep"}]', now());
            INSERT INTO chat_actions (action_id, turn_id, ordinal, actor_id, data_scope, operation,
                arguments, fingerprint, state, result, created_at)
            VALUES ('00000000-0000-0000-0000-000000000009', '00000000-0000-0000-0000-000000000008',
                0, 'owner', 'shared', 'radar.read', '{}', 'old', 'completed', '{}', now()),
                ('00000000-0000-0000-0000-000000000010', '00000000-0000-0000-0000-000000000008',
                1, 'owner', 'shared', 'file.read', '{}', 'keep', 'completed', '{"keep":true}', now());
        """)
        preserved = {table: connection.execute(text(f'SELECT to_jsonb(t) FROM {table} t')).scalars().all()
            for table in ('ai_tender_radar_jobs', 'artifacts')}
        command.upgrade(config, 'head')
        assert not {'radar_runs', 'radar_batches'} & set(inspect(connection).get_table_names(schema=schema))
        for table, before in preserved.items():
            assert connection.execute(text(f'SELECT to_jsonb(t) FROM {table} t')).scalars().all() == before
        for table in ('command_receipts', 'audit', 'chat_actions'):
            assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE operation LIKE 'radar.%'")).scalar() == 0
            assert connection.execute(text(f'SELECT count(*) FROM {table}')).scalar() == 1
        turn = connection.execute(text('SELECT question, answer, results FROM chat_turns')).one()
        assert tuple(turn) == ('ИИ-радар сохраняется', 'Ответ сохраняется', [{'kind': 'file', 'artifact_id': 'keep'}])
        connection.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
