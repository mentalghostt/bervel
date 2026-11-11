from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from bervel.core.api.app import STATUS, error_response
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.modules.ai_tender_radar.kontur_settings import kontur_settings_router
from bervel.modules.ai_tender_radar.infrastructure.kontur_credentials import KonturCredentials
from test_named_radar_execution import Source, environment, execute, tender


def limit(name, remaining, maximum):
    return {'LimitGroup': name, 'AvailableRequests': remaining, 'TotalLimit': maximum, 'IsPeriodic': True}


def test_valid_key_is_saved_privately_and_live_limits_are_refreshed(tmp_path):
    credentials = KonturCredentials(tmp_path, '')
    requests = []

    def limits(key):
        requests.append(key)
        if key == 'invalid-test-key':
            raise CoreError('KONTUR_AUTH_ERROR', 'Контур отклонил ключ доступа.')
        return [limit('search', 200 - len(requests), 200), limit('purchase', 157, 200), limit('results', 40, 50)]

    app = FastAPI()
    app.add_exception_handler(CoreError, lambda request, error: error_response(error.code, error.message, STATUS[error.code]))
    app.include_router(kontur_settings_router(credentials, lambda: ActorContext('operator', 'private',
        frozenset({'settings:read', 'settings:write'})), read_limits=limits))
    client = TestClient(app)
    path = '/api/v0.1/settings/kontur'

    assert client.get(path).json() == {'configured': False, 'limits': []}
    result = client.put(path, json={'api_key': 'private-test-key'})
    assert result.status_code == 200
    assert 'private-test-key' not in result.text
    assert result.json() == {'configured': True, 'limits': [
        {'group': 'search', 'remaining': 199, 'maximum': 200},
        {'group': 'purchase', 'remaining': 157, 'maximum': 200},
        {'group': 'results', 'remaining': 40, 'maximum': 50}]}
    assert KonturCredentials(tmp_path, 'environment-key').read() == 'private-test-key'
    assert client.get(path).json()['limits'][0]['remaining'] == 198
    assert client.put(path, json={'api_key': 'invalid-test-key'}).status_code == 502
    assert client.put(path, json={'api_key': 'bervel-kontur-simulator'}).status_code == 422
    assert credentials.read() == 'private-test-key'


def test_simulator_accounts_for_provider_remaining_across_restarts(tmp_path):
    from kontur_simulator import create_server

    quota_state = tmp_path / 'quota.json'
    limits = [limit('search', 3, 200), limit('purchase', 2, 200)]
    with create_server(port=0, max_requests_per_second=0, require_real_quota=True, quota_state=quota_state) as simulator:
        assert simulator.admit('purchase') == 429
        simulator.synchronize_quota(limits)
        assert simulator.admit('purchase') == 200
        assert simulator.admit('search') == 200
    with create_server(port=0, max_requests_per_second=0, require_real_quota=True, quota_state=quota_state) as simulator:
        simulator.synchronize_quota(limits)
        assert simulator.admit('purchase') == 200
        assert simulator.admit('purchase') == 429
        assert simulator.admit('search') == 200
        simulator.synchronize_quota([limit('search', 2, 200), limit('purchase', 2, 200)])
        assert simulator.admit('search') == 429


def test_mirror_only_reads_official_quotas_and_sends_them_to_local_simulator():
    from threading import Thread

    from kontur_simulator import create_server
    from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient, SIMULATOR_KEY

    limits = [limit('search', 3, 200), limit('purchase', 2, 200), limit('results', 200, 200)]
    read_requests = []

    def read_limits():
        read_requests.append('quota')
        return limits

    with create_server(port=0, max_requests_per_second=0, require_real_quota=True) as simulator:
        thread = Thread(target=simulator.serve_forever, daemon=True)
        thread.start()
        try:
            client = KonturClient(SIMULATOR_KEY, base_url=f'http://127.0.0.1:{simulator.server_port}',
                                  min_interval=0, provider_limits=read_limits)
            assert {row['LimitGroup']: row['AvailableRequests'] for row in client.read_limits()} == {'search': 3, 'purchase': 2}
            assert simulator.admit('purchase') == 200
            assert {row['LimitGroup']: row['AvailableRequests'] for row in client.read_limits()} == {'search': 3, 'purchase': 1}
            assert read_requests == ['quota', 'quota']
        finally:
            simulator.shutdown()
            thread.join(timeout=3)


def test_mirrored_quota_never_fetches_201st_detail_and_refreshes_new_period(monkeypatch):
    from kontur_simulator import create_server

    period = ['2026-11-06']
    limits = [limit('search', 200, 200), limit('purchase', 200, 200)]
    with create_server(port=0, profile='selection', max_requests_per_second=0, require_real_quota=True) as simulator:
        monkeypatch.setattr(simulator, '_quota_window', lambda row: period.copy())
        simulator.synchronize_quota(limits)
        assert all(simulator.admit('purchase') == 200 for _ in range(200))
        assert simulator.admit('purchase') == 429
        simulator.synchronize_quota(limits)
        assert simulator.admit('purchase') == 429
        period[0] = '2026-11-07'
        assert simulator.admit('search') == 429
        simulator.synchronize_quota(limits)
        assert simulator.admit('purchase') == 200


def test_requested_detail_limit_scales_search_and_is_cumulative():
    from bervel.modules.ai_tender_radar.named_runs import _run_budgets

    limits = [limit('search', 200, 200), limit('purchase', 200, 200)]
    used = {'search': 0, 'detail': 0}
    unknown = {'search': 0, 'detail': 0}
    assert _run_budgets(limits, used, unknown, None) == {'search': 200, 'detail': 200}
    assert _run_budgets(limits, used, unknown, 50) == {'search': 50, 'detail': 50}
    limits = [limit('search', 20, 100), limit('purchase', 100, 200)]
    assert _run_budgets(limits, used, unknown, 50) == {'search': 20, 'detail': 50}
    assert _run_budgets(limits, {'search': 12, 'detail': 49}, unknown, 50) == {'search': 25, 'detail': 50}
    assert _run_budgets(limits, used, unknown, 1) == {'search': 1, 'detail': 1}


def test_named_run_reads_only_selected_number_of_full_cards(environment):
    class QuotaSource(Source):
        def read_limits(self):
            return [limit('search', 100, 100), limit('purchase', 200, 200)]

    _, pipeline, _, run, _, _ = environment
    source = pipeline.sources['test_api'] = QuotaSource([tender(str(index)) for index in range(205)])
    run['data']['budgets'] = {'detail': 4}
    execute(environment)
    assert len(source.searches) == 2
    assert len(source.fetches) == 4
    assert pipeline.repository.state['state'] == 'complete'
    assert pipeline.repository.state['stop_reason'] == 'requested_detail_limit'
    assert pipeline.repository.state['unverified_count'] == 96
