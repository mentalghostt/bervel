from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy import select

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs
from test_ai_tender_radar_jobs import API, command, scenario, stock


def test_standard_loaded_job_exposes_saved_unit_prices_without_reclassifying_archive(scenario):
    s = scenario
    ref = stock(s)
    tender = s.kontur.fetch_tender('standard-api-card')
    tender['positions'][0]['quantity'] = '10'
    s.kontur.fetch_next = lambda phrase, processed: deepcopy(tender)
    receipt = s.client.post(API, json=command(ref, key='standard-prices'))
    assert receipt.status_code == 202
    job_id = UUID(receipt.json()['job_id'])
    assert s.service.run_once()
    with s.engine.connect() as connection:
        before = dict(connection.execute(select(jobs).where(jobs.c.job_id == job_id)).mappings().one())
    assert before['state'] == 'loaded'
    assert 'source' not in before['snapshot']
    assert s.store.archive_snapshot(s.actor.data_scope, job_id) is None

    response = s.client.get(f'{API}/{job_id}')
    assert response.status_code == 200
    line = response.json()['analysis']['positions'][0]
    assert line.get('unit_prices') == {
        'stock_cost': '12.34', 'tender_price': '10', 'currency': 'RUB', 'unit': 'шт'}
    assert s.store.saved_tender(s.actor.data_scope, job_id) == tender
    assert s.store.saved_tender('other-scope', job_id) is None
    assert s.store.archive_snapshot(s.actor.data_scope, job_id) is None
    with s.engine.connect() as connection:
        after = dict(connection.execute(select(jobs).where(jobs.c.job_id == job_id)).mappings().one())
    assert after == before


@pytest.mark.parametrize('code', ['NOT_FOUND', 'TRANSIENT_DEPENDENCY_ERROR', 'FORBIDDEN', 'STALE_REVISION'])
def test_saved_legacy_job_survives_only_expected_stock_read_failures(scenario, monkeypatch, code):
    s = scenario
    ref = stock(s)
    receipt = s.client.post(API, json=command(ref, key='legacy-stock-unavailable'))
    assert receipt.status_code == 202
    job_id = UUID(receipt.json()['job_id'])
    assert s.service.run_once()
    saved = s.store.read(s.actor.data_scope, job_id)
    assert saved['state'] == 'loaded'

    def unavailable(*args, **kwargs):
        raise CoreError(code, 'Original stock cannot be read')

    monkeypatch.setattr(s.artifacts, 'read', unavailable)
    if code in {'NOT_FOUND', 'TRANSIENT_DEPENDENCY_ERROR'}:
        response = s.client.get(f'{API}/{job_id}')
        assert response.status_code == 200
        assert response.json()['analysis'] == saved['analysis']
        assert 'unit_prices' not in response.json()['analysis']['positions'][0]
    else:
        with pytest.raises(CoreError) as error:
            s.service.read(s.actor, job_id)
        assert error.value.code == code
    assert s.store.read(s.actor.data_scope, job_id) == saved
