from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from bervel.core.models import ActorContext
from bervel.core.process_models import WorkflowDefinition
from test_processes import FakeEngine
from test_api import client

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/core-stage-3.1'


@pytest.fixture
def process_client(pg_engine, tmp_path):
    from http_client import ASGITestClient
    from bervel.api import create_app
    from bervel.config import Settings
    permissions = frozenset({'artifact:write', 'artifact:read', 'dataset:publish', 'dataset:read', 'module:read',
                             'process:start', 'process:read', 'process:recover'})
    keys = {uuid4().hex: ActorContext('operator', 'area-a', permissions),
            uuid4().hex: ActorContext('foreign', 'area-b', permissions),
            uuid4().hex: ActorContext('reader', 'area-a', frozenset({'artifact:read', 'dataset:read', 'process:read'}))}
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), tmp_path / 'objects', keys,
                        process_worker_key='w' * 32)
    engine = FakeEngine(WorkflowDefinition('workflow-demo', 'published-v1', '1.0.0'))
    with ASGITestClient(create_app(settings, workflow_engine=engine)) as client:
        yield client, list(keys), engine


def auth(key):
    return {'Authorization': 'Bearer ' + key}


def start(client, key):
    artifact = client.post('/api/v0.1/artifacts', headers=auth(key), data={'command_key': 'upload'},
        files={'file': ('v1.csv', (FIXTURES / 'requirements-v1.csv').read_bytes(), 'text/csv')}).json()
    body = {'artifact_id': artifact['artifact_id'], 'artifact_revision': 1, 'command_key': 'start'}
    response = client.post('/api/v0.1/demo/document-review/processes', headers=auth(key), json=body)
    assert response.status_code == 202, response.text
    return response.json(), body


def test_async_api_worker_separation_result_and_list(process_client):
    client, keys, engine = process_client
    process, body = start(client, keys[0])
    path = '/api/v0.1/processes/' + process['process_run_id']
    callback = '/api/internal/v0.1/processes/' + process['process_run_id']
    payload = {'execution_id': '100', 'invocation_token': engine.execution('100').invocation_token}
    assert client.post(callback + '/claim', headers=auth(keys[0]), json=payload).status_code == 401
    assert client.get(path, headers=auth('w' * 32)).status_code == 401
    assert client.post(callback + '/claim', headers=auth('w' * 32), json=payload).status_code == 200
    result = client.post(callback + '/execute', headers=auth('w' * 32), json=payload)
    assert result.status_code == 200, result.text
    assert client.post(callback + '/execute', headers=auth('w' * 32), json=payload).json() == result.json()
    assert client.get(path, headers=auth(keys[0])).json()['state'] == 'COMPLETED'
    assert client.get(path, headers=auth(keys[1])).status_code == 404
    listing = client.get('/api/v0.1/processes?state=COMPLETED&limit=1', headers=auth(keys[0])).json()
    assert listing['processes'][0]['result'] == result.json()
    assert client.get('/api/v0.1/processes', headers=auth(keys[1])).json()['processes'] == []
    assert client.post('/api/v0.1/demo/document-review/processes', headers=auth(keys[0]), json=body).json()['process_run_id'] == process['process_run_id']
    assert 'invocation_token' not in client.get(path, headers=auth(keys[0])).text


def test_process_api_permissions_recovery_validation_and_conflict(process_client):
    client, keys, engine = process_client
    process, body = start(client, keys[0])
    route = '/api/v0.1/processes/' + process['process_run_id']
    assert client.post('/api/v0.1/demo/document-review/processes', headers=auth(keys[2]), json=body).status_code == 403
    assert client.post(route + '/reconcile', headers=auth(keys[2])).status_code == 403
    assert client.get('/api/v0.1/processes?limit=101', headers=auth(keys[0])).status_code == 422
    assert client.post(route + '/recover', headers=auth(keys[0]), json={'command_key': 'r', 'expected_attempt': 1, 'reason': ''}).status_code == 422
    callback = '/api/internal/v0.1/processes/' + process['process_run_id']
    payload = {'execution_id': '100', 'invocation_token': engine.execution('100').invocation_token}
    client.post(callback + '/claim', headers=auth('w' * 32), json=payload)
    assert client.post(route + '/recover', headers=auth(keys[0]), json={
        'command_key': 'r', 'expected_attempt': 1, 'reason': 'still active'}).status_code == 409
    engine.executions['100'] = replace(engine.execution('100'), state='crashed')
    recovered = client.post(route + '/recover', headers=auth(keys[0]), json={
        'command_key': 'r', 'expected_attempt': 1, 'reason': 'crashed'})
    assert recovered.status_code == 202, recovered.text
    assert recovered.json()['active_attempt'] == 2
    assert client.post(route + '/recover', headers=auth(keys[0]), json={
        'command_key': 'r', 'expected_attempt': 1, 'reason': 'different'}).status_code == 409
    assert client.post(callback + '/execute', headers=auth('w' * 32), json=payload).status_code == 409


def test_orchestration_optional_existing_sync_api_kept(client):
    client, keys, _ = client
    assert client.get('/health/ready').status_code == 200
    assert client.get('/api/v0.1/processes', headers=auth(keys[0])).status_code in {403, 503}
