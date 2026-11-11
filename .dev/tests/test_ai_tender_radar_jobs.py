from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from hashlib import sha256
import json
import sys
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text

from bervel.core.api.app import create_platform_app
from bervel.core.config import Settings
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, RegisterArtifact
from bervel.modules.ai_tender_radar.api import ai_tender_radar_router
from bervel.modules.ai_tender_radar.infrastructure.archive import read_archive
from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender
from bervel.modules.ai_tender_radar.infrastructure.repository import PostgresAiTenderRadarRepository, jobs
from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
from bervel.modules.ai_tender_radar.service import AiTenderRadar
from test_ai_tender_radar_analysis import SelectingModel


API = '/api/v0.1/ai-tender-radar/jobs'
CARDS = '/api/v0.1/ai-tender-radar/cards'
ARCHIVES = '/api/v0.1/ai-tender-radar/jobs/from-file'
STOCK = 'sku,product,free_quantity,unit,unit_cost,currency\nA,Болт М12,10,шт,12.34,RUB\n'.encode()


class FakeKontur:
    def __init__(self):
        self.calls = []
        self.details = []
        self.failure = None

    def fetch_tender(self, key):
        self.details.append(key)
        if self.failure:
            raise self.failure
        lots = [{'JointObjects': [{'Name': 'Болт М12', 'Quantity': 7, 'Okei': 'шт', 'PositionPrice': 100}]}]
        return normalize_tender({'Id': key, 'Currency': 'RUB'}, {'Id': key, 'Title': 'Крепёж', 'Lots': lots})

    def fetch_next(self, phrase, processed_ids):
        self.calls.append(phrase)
        if self.failure:
            raise self.failure
        keys = ['СБ-1', '2']
        seen = processed_ids(keys)
        for key in keys:
            if key not in seen:
                lots = [{'JointObjects': [{'Name': 'Болт М12', 'Quantity': 7, 'Okei': 'шт'}]}, {}]
                return normalize_tender({'Id': key}, {'Id': key, 'Title': 'Крепёж', 'Lots': lots})
        return None


@pytest.fixture
def scenario(services, pg_engine, tmp_path):
    artifacts, _, _, original = services
    actor = ActorContext(original.actor_id, original.data_scope, original.permissions | {'process:read', 'process:start'})
    outsider = ActorContext('other', 'other-scope', actor.permissions)
    reader = ActorContext('reader', actor.data_scope, actor.permissions - {'process:start'})
    starter = ActorContext('starter', actor.data_scope, actor.permissions - {'process:read'})
    keys = {'a' * 32: actor, 'b' * 32: outsider, 'c' * 32: reader, 'd' * 32: starter}
    settings = Settings('unused', tmp_path / 'objects', keys)
    app, dependency = create_platform_app(settings, pg_engine, artifacts)
    kontur = FakeKontur()
    store = PostgresAiTenderRadarRepository(pg_engine)
    model = SelectingModel()
    service = AiTenderRadar(store, artifacts, kontur, read_stock, model, read_archive)
    app.include_router(ai_tender_radar_router(service, dependency))
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer ' + 'a' * 32
        yield SimpleNamespace(client=client, kontur=kontur, service=service, store=store, actor=actor,
                              artifacts=artifacts, engine=pg_engine, model=model)


def stock(scenario, content=STOCK, role='stock'):
    ref = scenario.artifacts.register(scenario.actor,
        RegisterArtifact(content, 'stock.csv', 'text/csv', {}, str(uuid4())))
    scenario.artifacts.set_usage_role(scenario.actor, ref.artifact_id, role)
    return ref


def command(ref, key='first', phrase='болт'):
    return {'stock_artifact_id': str(ref.artifact_id), 'command_key': key, 'search_phrase': phrase}


def test_file_batch_runs_all_unique_cards_and_reuses_archive_for_reanalysis(scenario):
    ref = stock(scenario)
    def card(key):
        return {'Id': key, 'Title': key, 'Lots': [{'JointObjects': [
            {'Name': 'Болт М12', 'Quantity': 2, 'Okei': 'шт', 'PositionPrice': 50},
        ]}]}
    raw = {'responses': [
        {'path': '/external/v1/search', 'body': json.dumps({'Items': [
            {'Id': 'first', 'Currency': 'RUB'}, {'Id': 'second', 'Currency': 'RUB'},
        ]})},
        *({'path': '/external/v1/purchases/' + key, 'body': json.dumps(card(key))}
          for key in ('first', 'second', 'first')),
    ]}
    archive = scenario.artifacts.register(scenario.actor, RegisterArtifact(
        json.dumps(raw).encode(), 'archive.json', 'application/json', {}, str(uuid4())))
    body = {'stock_artifact_id': str(ref.artifact_id), 'source_artifact_id': str(archive.artifact_id),
            'command_key': 'archive'}
    response = scenario.client.post(ARCHIVES, json=body)
    assert response.status_code == 202, response.text
    first = response.json()['job_id']
    assert response.json()['total'] == 2
    assert scenario.client.post(ARCHIVES, json=body).json()['job_id'] == first
    assert scenario.client.post(API, json=command(ref, key='api')).status_code == 409
    assert scenario.service.run_once()
    second = scenario.client.get(API + '/active').json()
    assert second['job_id'] != first
    assert second['batch_id'] == first
    assert scenario.service.run_once()
    assert scenario.client.get(API + '/active').json() is None
    cards = scenario.client.get(CARDS).json()['cards']
    assert {row['tender']['id'] for row in cards} == {'first', 'second'}
    assert all(row['benefit_percent'] == '50.64' for row in cards)
    assert scenario.kontur.calls == scenario.kontur.details == []
    recheck = scenario.client.post(f'{CARDS}/{first}/reanalyze', json={
        'stock_artifact_id': str(ref.artifact_id), 'command_key': 'recheck-file'})
    assert recheck.status_code == 202
    assert scenario.service.run_once()
    assert scenario.client.get(f"{API}/{recheck.json()['job_id']}").json()['tender']['id'] == 'first'
    assert scenario.kontur.details == []


def test_file_search_listing_is_rejected_before_creating_a_job(scenario):
    ref = stock(scenario)
    archive = scenario.artifacts.register(scenario.actor, RegisterArtifact(
        b'{"Items":[{"Id":"first","OrderName":"A"}]}', 'search.json', 'application/json', {}, str(uuid4())))
    body = {'stock_artifact_id': str(ref.artifact_id), 'source_artifact_id': str(archive.artifact_id),
            'command_key': 'search-only'}
    response = scenario.client.post(ARCHIVES, json=body)
    assert response.status_code == 422
    assert scenario.client.get(API + '/active').json() is None


def test_file_batch_advances_after_an_interrupted_job(scenario):
    ref = stock(scenario)
    raw = [{'Id': key, 'Title': key, 'Lots': [{'JointObjects': [
        {'Name': 'Болт М12', 'Quantity': 2, 'Okei': 'шт'},
    ]}]} for key in ('first', 'second')]
    archive = scenario.artifacts.register(scenario.actor, RegisterArtifact(
        json.dumps(raw).encode(), 'batch.json', 'application/json', {}, str(uuid4())))
    body = {'stock_artifact_id': str(ref.artifact_id), 'source_artifact_id': str(archive.artifact_id),
            'command_key': 'interrupted'}
    receipt = scenario.client.post(ARCHIVES, json=body).json()
    claimed = scenario.store.claim()
    assert str(claimed['job_id']) == receipt['job_id']
    with scenario.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == claimed['job_id']).values(
            lease_until=text("now() - interval '1 second'")))
    assert scenario.service.run_once()
    assert scenario.client.get(f"{API}/{receipt['job_id']}").json()['state'] == 'failed'
    assert [row['tender']['id'] for row in scenario.client.get(CARDS).json()['cards']] == ['second']
    assert scenario.client.get(API + '/active').json() is None


def test_one_job_loads_both_snapshots_and_next_job_skips_its_id(scenario):
    # This app has only the independent router. Its job also runs with the old queue absent.
    with scenario.engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS radar_runs CASCADE'))
    client = scenario.client
    ref = stock(scenario)
    body = command(ref)
    accepted = client.post(API, json=body)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()['job_id']
    assert accepted.json()['state'] == 'queued'
    assert scenario.kontur.calls == []
    assert client.get(API + '/active').json()['job_id'] == job_id
    assert client.post(API, json=body).json()['job_id'] == job_id
    assert client.post(API, json=command(ref, phrase='гайка')).json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    assert client.post(API, json=command(ref, key='different')).json()['error']['code'] == 'PROCESS_ACTIVE'

    assert scenario.service.run_once()
    assert client.get(API + '/active').json() is None
    result = client.get(f'{API}/{job_id}').json()
    assert result['state'] == 'loaded'
    assert result['tender']['id'] == 'СБ-1'
    assert result['tender']['limitations'] == [{'code': 'LOT_OBJECTS_MISSING', 'lot': 2}]
    assert result['stock']['sha256'] == sha256(STOCK).hexdigest()
    assert result['stock']['row_count'] == 1
    assert result['analysis']['positions'][0]['stock_status'] == 'full'
    assert result['analysis']['positions'][0]['covered'] == '7'
    assert result['analysis']['positions'][0]['allocations'][0]['sku'] == 'A'
    assert result['analysis']['coverage_percent'] is None  # Second lot is missing.
    assert result['analysis']['status'] == 'insufficient_data'
    assert len(scenario.model.calls) == 1
    with scenario.engine.connect() as conn:
        saved = conn.execute(select(jobs.c.snapshot).where(jobs.c.job_id == UUID(job_id))).scalar_one()
    assert 'stock' not in saved  # The file service remains the owner of the entire CSV.
    assert saved['tender']['positions'][0]['quantity'] == '7'

    second = client.post(API, json=command(ref, key='second')).json()['job_id']
    assert scenario.service.run_once()
    assert client.get(f'{API}/{second}').json()['tender']['id'] == '2'
    third = client.post(API, json=command(ref, key='third')).json()['job_id']
    assert scenario.service.run_once()
    assert client.get(f'{API}/{third}').json()['state'] == 'no_match'
    assert not scenario.service.run_once()


def test_scope_permissions_and_invalid_stock_before_kontur(scenario):
    client = scenario.client
    ref = stock(scenario, b'sku,product,quantity,unit\nA,Bolt,1,pc\n')
    request = command(ref)
    other = {'Authorization': 'Bearer ' + 'b' * 32}
    reader = {'Authorization': 'Bearer ' + 'c' * 32}
    assert client.post(API, json=request, headers=other).status_code == 404
    assert client.post(API, json=request, headers=reader).status_code == 403
    invalid = scenario.artifacts.register(scenario.actor, RegisterArtifact(b'not a CSV','notes.txt','text/plain',{},str(uuid4())))
    assert client.post(API, json=command(invalid)).status_code == 422
    accepted = client.post(API, json=request).json()
    url = f"{API}/{accepted['job_id']}"
    assert client.get(url, headers=other).status_code == 404
    assert client.get(API + '/active', headers=other).json() is None
    assert client.get(url, headers=reader).status_code == 200
    assert scenario.service.run_once()
    assert client.get(url).json()['error']['code'] == 'VALIDATION_ERROR'
    assert scenario.kontur.calls == []


def test_unlabelled_csv_is_accepted_and_analysed_without_assigning_a_role(scenario):
    ref = stock(scenario, role='general')
    response = scenario.client.post(API, json=command(ref))
    assert response.status_code == 202
    assert scenario.service.run_once()
    assert scenario.client.get(f"{API}/{response.json()['job_id']}").json()['state'] == 'loaded'
    assert scenario.artifacts.metadata(scenario.actor,ref)['usage_role'] == 'general'


def test_failed_request_is_terminal_and_interrupted_job_is_not_replayed(scenario):
    ref = stock(scenario)
    scenario.kontur.failure = CoreError('KONTUR_RATE_LIMIT', 'Rate limit')
    failed = scenario.client.post(API, json=command(ref)).json()['job_id']
    assert scenario.service.run_once()
    assert not scenario.service.run_once()
    assert scenario.client.get(f'{API}/{failed}').json()['error']['code'] == 'KONTUR_RATE_LIMIT'
    pending = scenario.client.post(API, json=command(ref, key='interrupted')).json()['job_id']
    assert scenario.store.claim()['job_id'] == UUID(pending)
    with scenario.engine.begin() as conn:
        conn.execute(jobs.update().where(jobs.c.job_id == UUID(pending)).values(lease_until=text("now() - interval '1 second'")))
    assert not scenario.service.run_once()
    assert scenario.client.get(f'{API}/{pending}').json()['error']['code'] == 'AI_RADAR_JOB_INTERRUPTED'
    assert scenario.kontur.calls == ['болт']
    assert scenario.client.post(API, json=command(ref, key='new-attempt')).status_code == 202


def test_replaying_enqueue_does_not_expose_a_completed_result_without_read_permission(scenario):
    headers = {'Authorization': 'Bearer ' + 'd' * 32}
    body = command(stock(scenario))
    accepted = scenario.client.post(API, json=body, headers=headers)
    assert accepted.status_code == 202
    job_id = accepted.json()['job_id']
    assert scenario.service.run_once()
    assert scenario.client.get(f'{API}/{job_id}', headers=headers).status_code == 403
    assert scenario.client.post(API, json=body, headers=headers).json() == {'job_id': job_id, 'state': 'loaded'}


def test_lists_cards_and_reanalysis_keeps_last_usable_result(scenario):
    client = scenario.client
    ref = stock(scenario)
    first = client.post(API, json=command(ref)).json()['job_id']
    assert scenario.service.run_once()
    card = client.get(CARDS).json()['cards'][0]
    assert card['job_id'] == first and card['tender']['id'] == 'СБ-1'
    assert 'positions' not in card
    assert card['coverage_percent'] is None
    assert Decimal(card['position_coverage_percent']) == 100
    assert card['benefit_reason'] == 'TENDER_PRICES_NOT_DISCLOSED'
    assert client.get(CARDS, headers={'Authorization': 'Bearer ' + 'b' * 32}).json()['cards'] == []

    body = {'stock_artifact_id': str(ref.artifact_id), 'command_key': 'recheck'}
    response = client.post(f'{CARDS}/{first}/reanalyze', json=body)
    assert response.status_code == 202, response.text
    new_job = response.json()['job_id']
    assert new_job != first
    assert client.post(f'{CARDS}/{first}/reanalyze', json=body).json()['job_id'] == new_job
    assert client.post(API, json=command(ref, key='recheck')).status_code == 409
    assert scenario.service.run_once()
    assert scenario.kontur.calls == ['болт'] and scenario.kontur.details == ['СБ-1']
    cards = client.get(CARDS).json()['cards']
    assert [card['job_id'] for card in cards] == [new_job]
    assert cards[0]['coverage_percent'] == '100'
    assert cards[0]['benefit_percent'] == '13.62'
    assert cards[0]['benefit_reason'] is None

    scenario.model.edit = lambda _: (_ for _ in ()).throw(CoreError('MODEL_TIMEOUT', 'Timeout'))
    partial = client.post(f'{CARDS}/{new_job}/reanalyze', json=body | {'command_key': 'later'}).json()['job_id']
    assert scenario.service.run_once()
    assert client.get(f'{API}/{partial}').json()['analysis']['status'] == 'partial'
    assert [card['job_id'] for card in client.get(CARDS).json()['cards']] == [new_job]

    next_job = client.post(API, json=command(ref, key='next')).json()['job_id']
    assert scenario.service.run_once()
    page = client.get(CARDS, params={'limit': 1}).json()
    assert page['next_offset'] == 1
    assert page['cards'][0]['job_id'] == next_job
    older = client.get(CARDS, params={'limit': 1, 'offset': 1}).json()
    assert older['next_offset'] is None and older['cards'][0]['job_id'] == new_job


def test_card_fallback_counts_only_full_positions_and_checks_source_prices(scenario):
    ref = stock(scenario)
    body = command(ref)
    scenario.kontur.fetch_next = lambda *_: normalize_tender({'Id': 'partial'},
        {'Id': 'partial', 'Title': 'Крепёж', 'Lots': [{'JointObjects': [
            {'Name': 'Болт М12', 'Quantity': 4, 'Okei': 'шт'},
            {'Name': 'Болт М12', 'Quantity': 20, 'Okei': 'шт'},
            {'Name': 'Гайка М12', 'Quantity': 2, 'Okei': 'кг', 'ItemPrice': 50},
        ]}]})
    assert scenario.client.post(API, json=body).status_code == 202
    assert scenario.service.run_once()
    card = scenario.client.get(CARDS).json()['cards'][0]
    assert card['coverage_percent'] is None
    assert round(Decimal(card['position_coverage_percent']), 1) == Decimal('33.3')
    assert card['benefit_reason'] != 'TENDER_PRICES_NOT_DISCLOSED'


def test_card_sort_uses_displayed_numeric_values_before_paging(scenario):
    ref = stock(scenario)

    def bolt(quantity, unit='шт', **prices):
        return {'Name': 'Болт М12', 'Quantity': quantity, 'Okei': unit, **prices}

    for key, objects in (
        ('profit', [bolt(1, PositionPrice=20)]),
        ('loss', [bolt(1, PositionPrice=10)]),
        ('partial', [bolt(15)]),
        ('fallback', [bolt(1), bolt(1, 'кг')]),
        ('unknown', []),
    ):
        scenario.kontur.fetch_next = lambda *_: normalize_tender({'Id': key, 'Currency': 'RUB'},
            {'Id': key, 'Title': key, 'Lots': [{'JointObjects': objects}]})
        assert scenario.client.post(API, json=command(ref, key=key)).status_code == 202
        assert scenario.service.run_once()

    def page(sort, offset):
        response = scenario.client.get(CARDS, params={'sort': sort, 'limit': 2, 'offset': offset})
        assert response.status_code == 200
        return [card['tender']['id'] for card in response.json()['cards']]

    assert page('coverage', 0) == ['loss', 'profit']
    assert page('coverage', 2) == ['partial', 'fallback']
    assert page('coverage', 4) == ['unknown']
    assert page('benefit', 0) == ['profit', 'loss']
    assert page('benefit', 2) == ['unknown', 'fallback']


@pytest.mark.parametrize('same_key', [True, False])
def test_concurrent_enqueues_are_atomic(scenario, same_key):
    ref = stock(scenario)
    barrier = Barrier(2)

    def submit(key):
        barrier.wait(timeout=5)
        try:
            return scenario.service.enqueue(scenario.actor, ref, 'болт', key)['job_id']
        except CoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(submit, ['same', 'same' if same_key else 'other'])
    if same_key:
        assert first == second and isinstance(first, UUID)
    else:
        assert 'PROCESS_ACTIVE' in {first, second}
        assert sum(isinstance(value, UUID) for value in (first, second)) == 1


def test_dedicated_worker_cli_does_not_build_the_old_services(monkeypatch):
    from bervel.__main__ import main
    from bervel import worker

    events = []
    engine = SimpleNamespace(dispose=lambda: events.append('dispose'))
    service = SimpleNamespace(run_once=lambda: events.append('run') or True)
    settings = SimpleNamespace()
    monkeypatch.setattr(Settings, 'from_environment', lambda: settings)
    monkeypatch.setattr(worker, 'build_ai_tender_radar_worker', lambda supplied: (engine, service))
    monkeypatch.setattr(worker, 'build_services', lambda *args, **kwargs: pytest.fail('Old services are not needed'))
    monkeypatch.setattr(sys, 'argv', ['bervel', 'worker', '--module', 'ai_tender_radar', '--once'])
    assert main() == 0
    assert events == ['run', 'dispose']
