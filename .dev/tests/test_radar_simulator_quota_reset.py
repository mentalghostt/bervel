"""Local simulator reset never touches the real provider or tender storage."""
from threading import Thread

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from bervel.core.api.app import STATUS, error_response
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient
from bervel.modules.ai_tender_radar.kontur_settings import kontur_settings_router
from bervel.modules.ai_tender_radar.source_contract import SIMULATOR_KEY
from kontur_simulator import create_server


def test_reset_restores_both_finite_limits_without_changing_corpus():
    with create_server(port=0, profile='selection', max_requests_per_second=0, request_quota=3) as server:
        original = dict(server.purchases)
        assert server.admit('search') == server.admit('purchase') == 200
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = KonturClient(SIMULATOR_KEY, base_url=f'http://127.0.0.1:{server.server_port}', min_interval=0)
            assert {r['AvailableRequests'] for r in client.reset_simulator_quota()} == {3}
            assert {r['AvailableRequests'] for r in client.read_limits()} == {3}
            assert server.purchases == original
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_reset_keeps_unlimited_mode_and_refuses_provider_mirroring():
    with create_server(port=0, profile='selection', unlimited_requests=True) as server:
        assert server.reset_quota() == []
        assert server.remaining == {}
    with create_server(port=0, profile='selection', request_quota=3, require_real_quota=True) as server:
        with pytest.raises(ValueError):
            server.reset_quota()


def test_real_adapter_refuses_reset_before_any_io():
    class ForbiddenOpener:
        def open(self, *args, **kwargs):
            pytest.fail('A real provider must never receive reset')
    client = KonturClient('private-fixture-key', allow_configured_api=True, opener=ForbiddenOpener())
    with pytest.raises(CoreError):
        client.reset_simulator_quota()


class LocalSource:
    simulated = True
    key = SIMULATOR_KEY
    provider_limits = None

    def __init__(self):
        self.resets = 0

    def read_limits(self):
        return [{'LimitGroup': group, 'AvailableRequests': 3 if self.resets else 1,
                 'TotalLimit': 3, 'IsPeriodic': True} for group in ('search', 'purchase')]

    def reset_simulator_quota(self):
        self.resets += 1
        return self.read_limits()


def settings_client(source, permissions):
    class PrivateCredentials:
        def read(self):
            pytest.fail('Simulator settings must not read the real API key')
    app = FastAPI()
    app.add_exception_handler(CoreError, lambda request, error: error_response(error.code, error.message, STATUS[error.code]))
    app.include_router(kontur_settings_router(PrivateCredentials(),
        lambda: ActorContext('operator', 'scope', frozenset(permissions)), simulator=source))
    return TestClient(app)


def test_settings_reset_uses_only_local_source_and_requires_write_permission():
    source = LocalSource()
    client = settings_client(source, {'settings:read', 'settings:write'})
    path = '/api/v0.1/settings/kontur/simulation/quota'
    before = client.get(path)
    assert before.status_code == 200
    assert before.json()['limits'][0]['remaining'] == 1
    assert before.json()['unlimited'] is False
    assert source.resets == 0
    result = client.post(path + '/reset')
    assert result.status_code == 200
    assert result.json()['limits'][0]['remaining'] == 3
    assert source.resets == 1
    assert client.post(path + '/reset').status_code == 200
    assert source.resets == 2
    assert settings_client(source, {'settings:read'}).post(path + '/reset').status_code == 403
    assert source.resets == 2


def test_unconfigured_simulator_is_unavailable_and_cannot_be_reset():
    client = settings_client(None, {'settings:read', 'settings:write'})
    path = '/api/v0.1/settings/kontur/simulation/quota'
    assert client.get(path).json() == {'available': False, 'unlimited': False, 'resettable': False, 'limits': []}
    assert client.post(path + '/reset').status_code >= 400
