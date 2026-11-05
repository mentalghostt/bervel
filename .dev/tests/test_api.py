from pathlib import Path
from uuid import uuid4
import json

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures/core-stage-3.1'
PREFIX = '/api/v0.1'


@pytest.fixture
def client(pg_engine, tmp_path):
    from http_client import ASGITestClient
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from bervel.core.models import ActorContext
    permissions = frozenset({'artifact:write', 'artifact:read', 'dataset:publish', 'dataset:read', 'module:read'})
    keys = {uuid4().hex: ActorContext('actor-a', 'test-area-a', permissions),
            uuid4().hex: ActorContext('actor-b', 'test-area-b', permissions),
            uuid4().hex: ActorContext('reader', 'test-area-a', frozenset({'artifact:read', 'dataset:read'}))}
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), tmp_path / 'objects', keys)
    with ASGITestClient(create_app(settings)) as http:
        yield http, list(keys), settings


def auth(key):
    return {'Authorization': 'Bearer ' + key}


def upload(client, key, version=1, path='/artifacts', **data):
    return client.post(PREFIX + path, headers=auth(key), data={
        'command_key': f'upload-v{version}', 'origin_system': 'manual-test', **data},
        files={'file': (f'requirements-v{version}.csv', (FIXTURES / f'requirements-v{version}.csv').read_bytes(), 'text/csv')})


def test_original_ids_retries_and_independent_uploads(client):
    http, keys, _ = client
    assert http.get('/docs').status_code == 200
    assert http.get('/health/live').json() == {'status': 'ok'}
    assert http.get('/health/ready').status_code == 200
    first = upload(http, keys[0]).json()
    assert set(first) == {'artifact_id'} and upload(http, keys[0]).json() == first
    refpath = f"/artifacts/{first['artifact_id']}"
    assert http.get(PREFIX + refpath + '/content', headers=auth(keys[0])).content == (FIXTURES / 'requirements-v1.csv').read_bytes()
    assert http.get(PREFIX + refpath, headers=auth(keys[0])).json()['source']['external_id'] is None
    second = upload(http, keys[0], 2).json()
    assert set(second) == {'artifact_id'} and second['artifact_id'] != first['artifact_id']
    assert http.get(PREFIX + f"/artifacts/{second['artifact_id']}/content", headers=auth(keys[0])).content == (FIXTURES / 'requirements-v2.csv').read_bytes()
    assert http.get(PREFIX + refpath + '/content', headers=auth(keys[0])).content == (FIXTURES / 'requirements-v1.csv').read_bytes()
    assert http.get(PREFIX + refpath + '/history', headers=auth(keys[0])).status_code == 404
    assert http.get(PREFIX + refpath + '/revisions/1', headers=auth(keys[0])).status_code == 404
    assert upload(http, keys[0], 2, refpath + '/revisions', expected_revision='1').status_code == 404


def test_foreign_scope_unknown_key_and_permissions(client):
    http, keys, _ = client
    first = upload(http, keys[0]).json()
    paths = [f"/artifacts/{first['artifact_id']}", f"/artifacts/{first['artifact_id']}/content"]
    for path in paths:
        response = http.get(PREFIX + path, headers=auth(keys[1]))
        assert response.status_code == 404
        assert 'object_ref' not in response.text and 'test-area-a' not in response.text
        assert http.get(PREFIX + path, headers=auth('unrecognized')).status_code == 401
        assert http.get(PREFIX + path).status_code == 401
    assert upload(http, keys[2]).status_code == 403
    assert upload(http, keys[1], data_scope='test-area-a', actor_id='actor-a').json()['artifact_id'] != first['artifact_id']


def test_original_upload_limits_validation_and_error_redaction(client):
    http, keys, _ = client
    def send(content, **data):
        return http.post(PREFIX + '/artifacts', headers=auth(keys[0]), data={'command_key': uuid4().hex, **data}, files={'file': ('source.csv', content, 'text/csv')})
    assert send(b'other;headers\n1;2\n').status_code == 200
    for size in (5 * 1024 * 1024 + 1, 5 * 1024 * 1024 + 70 * 1024):
        response=send(b'x' * size)
        assert response.status_code == 200, response.text
        original = http.get(PREFIX + '/artifacts/' + response.json()['artifact_id'] + '/content', headers=auth(keys[0]))
        assert original.status_code == 200 and original.content == b'x' * size
    invalid=send(b'bytes', received_at='not-a-date')
    assert invalid.status_code == 422 and 'Traceback' not in invalid.text and keys[0] not in invalid.text
    assert send(b'bytes', source_role='original_request').status_code == 422
    assert http.get(PREFIX + '/artifacts/not-a-uuid', headers=auth(keys[0])).status_code == 422


def test_configuration_has_no_server_or_development_path_defaults(monkeypatch, tmp_path):
    from bervel.core.config import Settings
    monkeypatch.setenv('BERVEL_DATABASE_URL', 'postgresql+psycopg://app@db.example.invalid/app')
    monkeypatch.setenv('BERVEL_OBJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('BERVEL_ACCESS_KEYS', json.dumps([{'key': uuid4().hex, 'actor_id': 'actor', 'data_scope': 'area', 'permissions': ['artifact:read']}]))
    settings = Settings.from_environment()
    assert settings.object_root == tmp_path and settings.host == '127.0.0.1'
    monkeypatch.setenv('BERVEL_HOST', '0.0.0.0')
    assert Settings.from_environment().host == '0.0.0.0'


def test_openapi_documents_typed_response_contracts(client):
    http, _, _ = client
    schema = http.get('/openapi.json').json()
    response = schema['paths'][PREFIX + '/artifacts']['post']['responses']['200']['content']['application/json']['schema']
    assert response.get('$ref') == '#/components/schemas/ArtifactRefResponse'
    assert set(schema['components']['schemas']['ArtifactRefResponse']['properties']) == {'artifact_id'}
    assert schema['components']['schemas']['ArtifactRefResponse']['required'] == ['artifact_id']
    failure = schema['paths'][PREFIX + '/artifacts']['post']['responses']['422']['content']['application/json']['schema']
    assert failure.get('$ref') == '#/components/schemas/ErrorResponse'
