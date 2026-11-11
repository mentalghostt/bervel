from dataclasses import replace

from fastapi.testclient import TestClient

from bervel.app_setup import create_app
from bervel.core.config import Settings


def test_provider_settings_require_explicit_permissions_and_hide_key(pg_engine, tmp_path, monkeypatch):
    from bervel.core.infrastructure.codex import CodexClient
    calls = []
    public = {'provider': 'mox', 'api': {'base_url': 'https://api.openai.com/v1', 'model': '', 'api_key_configured': False, 'error': None},
              'mox': {'base_url': 'https://gateway.example/backend-api', 'model': 'model', 'credentials_available': True, 'error': None}, 'configured': True}
    def request(self, method, path, body=None):
        calls.append((method, path, body))
        return {'answer': 'OK', 'model': 'model'} if path.endswith('/test') else public
    monkeypatch.setattr(CodexClient, 'settings_request', request)
    from bervel.core.models import ActorContext
    actor = ActorContext('owner', 'scope', frozenset({'settings:read', 'settings:write'}))
    key = 's' * 32
    settings = Settings(str(pg_engine.url.render_as_string(hide_password=False)), tmp_path, {key: actor})
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/v0.1/settings/model').status_code == 401
        headers = {'Authorization': 'Bearer ' + key}
        assert client.get('/api/v0.1/settings/model', headers=headers).json() == public
        result = client.put('/api/v0.1/settings/model', headers=headers, json={'provider': 'api_key', 'model': 'model', 'base_url': 'https://api.openai.com/v1', 'api_key': 'fixture-private'})
        assert result.status_code == 200
        assert 'fixture-private' not in result.text
        assert client.post('/api/v0.1/settings/model/test', headers=headers, json={}).json()['answer'] == 'OK'
        assert client.put('/api/v0.1/settings/model', headers=headers, json={'provider': 'other', 'model': 'x'}).status_code == 422
    restricted = replace(settings, access_keys={key: replace(actor, permissions=frozenset({'chat:read', 'chat:write'}))})
    before = len(calls)
    with TestClient(create_app(restricted)) as client:
        assert client.get('/api/v0.1/settings/model', headers=headers).status_code == 403
        assert client.put('/api/v0.1/settings/model', headers=headers, json={'provider': 'mox', 'model': 'model'}).status_code == 403
        assert client.post('/api/v0.1/settings/model/test', headers=headers, json={}).status_code == 403
    assert len(calls) == before
