import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient
from test_kontur_simulator import request_simulator, search_body


def test_real_source_uses_search_and_cards_and_skips_processed_ids(request_simulator):
    client = KonturClient('bervel-kontur-simulator', base_url=request_simulator.url, min_interval=0)
    first = client.fetch_next('болт', lambda ids: set())
    assert first['id'] == 'SIM-00001'
    assert first['simulated'] is True
    second = client.fetch_next('болт', lambda ids: {first['id']})
    assert second['id'] != first['id']
    assert 'Обслуживание' in second['positions'][0]['name']
    assert client.fetch_next('нет такого товара', lambda ids: set()) is None


def test_source_preserves_three_page_window_and_reports_quota_failure(request_simulator):
    client = KonturClient('bervel-kontur-simulator', base_url=request_simulator.url, min_interval=0)
    assert client.fetch_next('закупка', set) is None
    status, groups = request_simulator('GET', 'limitGroups')
    assert status == 200
    assert {group['LimitGroup']: group['AvailableRequests'] for group in groups} == {'search': 197, 'purchase': 200}
    for _ in range(197):
        assert request_simulator('POST', 'search', search_body('нет такого товара'))[0] == 200
    with pytest.raises(CoreError) as error:
        client.fetch_next('болт', set)
    assert error.value.code == 'KONTUR_RATE_LIMIT'


@pytest.mark.parametrize('base_url,key', [
    ('https://example.com', 'bervel-kontur-simulator'),
    ('http://127.0.0.1:8765', 'production-key-must-stay-private'),
    ('http://127.0.0.1:8765/?redirect=1', 'bervel-kontur-simulator'),
    ('https://api-zakupki.kontur.ru', 'bervel-kontur-simulator'),
])
def test_misconfigured_source_is_rejected_before_network(base_url, key):
    with pytest.raises(ValueError):
        KonturClient(key, base_url=base_url)


def test_unavailable_simulator_is_a_connection_error_without_live_fallback():
    from kontur_simulator import create_server

    with create_server(port=0) as server:
        address = f'http://127.0.0.1:{server.server_port}'
    client = KonturClient('bervel-kontur-simulator', base_url=address, min_interval=0)
    with pytest.raises(CoreError) as error:
        client.fetch_tender('SIM-00001')
    assert error.value.code == 'KONTUR_UNAVAILABLE'


def test_http_job_worker_result_and_reanalysis_use_simulator(scenario, request_simulator):
    from bervel.ai_tender_radar_setup import build_ai_tender_radar
    from test_ai_tender_radar_jobs import API, CARDS, command, stock

    configured = build_ai_tender_radar(scenario.engine, scenario.artifacts, 'private-production-key', scenario.model,
                                       kontur_base_url=request_simulator.url)
    assert configured.kontur.key == 'bervel-kontur-simulator'
    configured.kontur.min_interval = 0
    scenario.service.kontur = configured.kontur
    ref = stock(scenario, ('sku,product,free_quantity,unit,unit_cost,currency,standard,length_mm,diameter_mm\n'
                          'B12,Болт М12,100000,шт,25,RUB,ГОСТ 7798-70,50,12\n').encode())
    receipt = scenario.client.post(API, json=command(ref, phrase='болт'))
    assert receipt.status_code == 202
    job_id = receipt.json()['job_id']
    assert scenario.service.run_once()
    result = scenario.client.get(f'{API}/{job_id}').json()
    assert result['state'] == 'loaded'
    assert result['analysis']['mock'] is True
    assert result['analysis']['coverage_percent'] == '100'
    assert result['analysis']['benefit_percent'] == '75'
    assert scenario.client.get(CARDS).json()['cards'][0]['mock'] is True
    reanalysis = scenario.client.post(f'{CARDS}/{job_id}/reanalyze', json={
        'stock_artifact_id': str(ref.artifact_id), 'command_key': 'recheck-simulator'})
    assert reanalysis.status_code == 202
    assert scenario.service.run_once()
    repeated = scenario.client.get(f"{API}/{reanalysis.json()['job_id']}").json()
    assert repeated['tender']['id'] == result['tender']['id']
    assert repeated['analysis']['mock'] is True
    assert scenario.client.post(API, json=command(ref, key='next', phrase='болт')).status_code == 202
    assert scenario.service.run_once()
    cards = scenario.client.get(CARDS).json()['cards']
    assert len(cards) == 2
    other = next(card for card in cards if card['tender']['id'] != result['tender']['id'])
    assert other['coverage_percent'] is None
    assert other['status'] == 'insufficient_data'
    scenario.service.kontur = type('LiveSource', (), {'simulated': False})()
    rejected = scenario.client.post(f'{CARDS}/{job_id}/reanalyze', json={
        'stock_artifact_id': str(ref.artifact_id), 'command_key': 'wrong-source'})
    assert rejected.status_code == 422


# Reuse the real PostgreSQL + HTTP fixture, replacing only its external tender source.
from test_ai_tender_radar_jobs import scenario
