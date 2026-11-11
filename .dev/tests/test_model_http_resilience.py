"""Real model transport failures retain a meaningful public HTTP status."""
from io import BytesIO
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient

from bervel.app_setup import create_app
from bervel.core.config import Settings
from bervel.core.models import ActorContext


@pytest.mark.parametrize('failure,expected,code', [('disconnect', 503, 'MODEL_UNAVAILABLE'),
    ('invalid_json', 502, 'MODEL_INVALID_OUTPUT')])
def test_settings_transport_failure_is_a_safe_gateway_response(pg_engine, tmp_path, monkeypatch, failure, expected, code):
    def external(*_args, **_kwargs):
        if failure == 'disconnect':
            raise URLError('PRIVATE fixture diagnostic')
        return BytesIO(b'PRIVATE malformed upstream response')
    monkeypatch.setattr('bervel.core.infrastructure.codex.urlopen', external)
    actor = ActorContext('owner', 'scope', frozenset({'settings:read'}))
    key = 's' * 32
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), tmp_path, {key: actor})
    with TestClient(create_app(settings)) as http:
        response = http.get('/api/v0.1/settings/model', headers={'Authorization': 'Bearer ' + key})
    assert response.status_code == expected
    assert response.json()['error']['code'] == code
    assert 'PRIVATE' not in response.text
