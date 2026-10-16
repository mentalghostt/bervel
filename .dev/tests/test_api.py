from dataclasses import replace
import json
from pathlib import Path
from uuid import uuid4

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/core-stage-3.1'
PREFIX = '/api/v0.1'


@pytest.fixture
def client(pg_engine, tmp_path):
    from http_client import ASGITestClient as TestClient
    from bervel.api import create_app
    from bervel.config import Settings
    from bervel.core.models import ActorContext
    permissions = frozenset({'artifact:write', 'artifact:read', 'dataset:publish', 'dataset:read', 'module:read'})
    keys = {uuid4().hex: ActorContext('actor-a', 'test-area-a', permissions),
            uuid4().hex: ActorContext('actor-b', 'test-area-b', permissions),
            uuid4().hex: ActorContext('reader', 'test-area-a', frozenset({'artifact:read', 'dataset:read'}))}
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), tmp_path / 'objects', keys)
    with TestClient(create_app(settings)) as http:
        yield http, list(keys), settings


def auth(key):
    return {'Authorization': 'Bearer ' + key}


def upload(client, key, version=1, path='/artifacts', **data):
    return client.post(PREFIX + path, headers=auth(key), data={
        'command_key': f'upload-v{version}', 'origin_system': 'manual-test', **data},
        files={'file': (f'requirements-v{version}.csv', (FIXTURES / f'requirements-v{version}.csv').read_bytes(), 'text/csv')})


def test_full_browser_contract_revisions_reads_pages_and_history(client):
    http, keys, settings = client
    assert http.get('/docs').status_code == 200
    assert http.get('/health/live').json() == {'status': 'ok'}
    assert http.get('/health/ready').status_code == 200
    assert http.get(PREFIX + '/modules', headers=auth(keys[0])).json()[0]['module_id'] == 'demo.document-review'
    first = upload(http, keys[0]).json()
    assert first['revision'] == 1
    assert upload(http, keys[0]).json() == first
    refpath = f"/artifacts/{first['artifact_id']}/revisions/1"
    original = http.get(PREFIX + refpath + '/content', headers=auth(keys[0]))
    assert original.content == (FIXTURES / 'requirements-v1.csv').read_bytes()
    metadata = http.get(PREFIX + refpath, headers=auth(keys[0])).json()
    assert metadata['source']['external_id'] is None
    body = {'artifact_id': first['artifact_id'], 'artifact_revision': 1, 'command_key': 'extract-v1', 'module_version': '1.0.0', 'expected_revision': 0}
    extracted = http.post(PREFIX + '/demo/document-review/extractions', headers=auth(keys[0]), json=body)
    assert extracted.status_code == 200
    result = extracted.json()
    assert http.post(PREFIX + '/demo/document-review/extractions', headers=auth(keys[0]), json=body).json() == result
    dataset = f"/datasets/{result['dataset_id']}/revisions/1"
    envelope = http.get(PREFIX + dataset, headers=auth(keys[0])).json()
    assert envelope['quality_state'] == 'PARTIAL'
    assert http.get(PREFIX + dataset + '/records?limit=1', headers=auth(keys[0])).json()['next_cursor'] == 1
    assert http.get(PREFIX + dataset + '/records?limit=1&cursor=1', headers=auth(keys[0])).json()['records'][0]['payload']['quantity']['value'] is None
    second = upload(http, keys[0], 2, f"/artifacts/{first['artifact_id']}/revisions", expected_revision='1').json()
    assert second['revision'] == 2
    secondbody = {**body, 'artifact_revision': 2, 'command_key': 'extract-v2', 'dataset_id': result['dataset_id'], 'expected_revision': 1}
    assert http.post(PREFIX + '/demo/document-review/extractions', headers=auth(keys[0]), json=secondbody).json()['revision'] == 2
    assert http.get(PREFIX + dataset + '/records', headers=auth(keys[0])).json()['records'][1]['payload']['quantity']['value'] is None
    history = http.get(PREFIX + f"/artifacts/{first['artifact_id']}/history", headers=auth(keys[0])).json()
    assert len(history['revisions']) == len(history['datasets']) == 2
    assert http.post(PREFIX + '/demo/document-review/extractions', headers=auth(keys[0]), json={**body, 'artifact_revision': 2}).status_code == 409
    assert http.post(PREFIX + '/demo/document-review/extractions', headers=auth(keys[0]), json={**secondbody, 'command_key': 'stale'}).json()['error']['code'] == 'STALE_REVISION'


def test_foreign_scope_unknown_key_and_permissions(client):
    http, keys, settings = client
    first = upload(http, keys[0]).json()
    result = http.post(PREFIX + '/demo/document-review/extractions', headers=auth(keys[0]), json={
        'artifact_id': first['artifact_id'], 'artifact_revision': 1, 'command_key': 'extract'}).json()
    paths = [f"/artifacts/{first['artifact_id']}/revisions/1", f"/artifacts/{first['artifact_id']}/revisions/1/content",
             f"/artifacts/{first['artifact_id']}/history", f"/datasets/{result['dataset_id']}/revisions/1",
             f"/datasets/{result['dataset_id']}/revisions/1/records"]
    for path in paths:
        response = http.get(PREFIX + path, headers=auth(keys[1]))
        assert response.status_code == 404
        assert 'object_ref' not in response.text and 'test-area-a' not in response.text
        assert http.get(PREFIX + path, headers=auth('unrecognized')).status_code == 401
        assert http.get(PREFIX + path).status_code == 401
    assert upload(http, keys[2]).status_code == 403
    assert http.post(PREFIX + '/demo/document-review/extractions', headers=auth(keys[0]), json={
        'artifact_id': first['artifact_id'], 'artifact_revision': 1, 'command_key': 'spoof', 'data_scope': 'test-area-b'}).status_code == 422
    assert upload(http, keys[1], data_scope='test-area-a', actor_id='actor-a').json()['artifact_id'] != first['artifact_id']


def test_invalid_upload_processing_limits_and_error_redaction(client):
    http, keys, settings = client
    def send(content, **data):
        return http.post(PREFIX + '/artifacts', headers=auth(keys[0]), data={'command_key': uuid4().hex, **data},
                         files={'file': ('invalid.csv', content, 'text/csv')})
    invalid = send(b'line,name,quantity,unit\n1,A,-1,pcs\n').json()
    response = http.post(PREFIX + '/demo/document-review/extractions', headers=auth(keys[0]), json={
        'artifact_id': invalid['artifact_id'], 'artifact_revision': 1, 'command_key': 'invalid-extract'})
    assert response.status_code == 422
    assert 'Traceback' not in response.text and keys[0] not in response.text
    assert send(b'x' * (5 * 1024 * 1024 + 1)).status_code == 413
    excessive = send(b'x' * (5 * 1024 * 1024 + 70 * 1024))
    assert excessive.status_code == 413
    assert excessive.json()['error']['code'] == 'QUOTA_EXCEEDED'
    assert send(b'bytes', source_role='original_request').status_code == 422
    assert send(b'bytes', received_at='not-a-date').status_code == 422
    assert http.get(PREFIX + '/artifacts/not-a-uuid/revisions/1', headers=auth(keys[0])).status_code == 422


def test_configuration_has_no_server_or_development_path_defaults(monkeypatch, tmp_path):
    from bervel.config import Settings
    monkeypatch.setenv('BERVEL_DATABASE_URL', 'postgresql+psycopg://app@db.example.invalid/app')
    monkeypatch.setenv('BERVEL_OBJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('BERVEL_ACCESS_KEYS', json.dumps([{
        'key': uuid4().hex, 'actor_id': 'actor', 'data_scope': 'area', 'permissions': ['artifact:read']}]))
    settings = Settings.from_environment()
    assert settings.object_root == tmp_path
    assert settings.host == '127.0.0.1'
    monkeypatch.setenv('BERVEL_HOST', '0.0.0.0')
    assert Settings.from_environment().host == '0.0.0.0'


def test_openapi_documents_typed_response_contracts(client):
    http, keys, settings = client
    schema = http.get('/openapi.json').json()
    response = schema['paths'][PREFIX + '/artifacts']['post']['responses']['200']['content']['application/json']['schema']
    assert response.get('$ref') == '#/components/schemas/ArtifactRefResponse'
    assert {'artifact_id', 'revision'} <= set(schema['components']['schemas']['ArtifactRefResponse']['required'])
    failure = schema['paths'][PREFIX + '/artifacts']['post']['responses']['422']['content']['application/json']['schema']
    assert failure.get('$ref') == '#/components/schemas/ErrorResponse'
