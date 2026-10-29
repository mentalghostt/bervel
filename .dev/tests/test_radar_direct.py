"""Direct-screening orchestration; fake model replies do not measure LLM accuracy."""
import csv
import io
import json
from uuid import UUID

import pytest

from test_radar import radar, add


def procurement_csv(count):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['number', 'title', 'deadline_at', 'source_url', 'stage'])
    for number in range(count):
        # No lexical overlap; expired and undated rows must both reach the model.
        writer.writerow([str(number), f'Закупка оборудования {number}',
                         '2020-01-01T00:00:00Z' if number % 2 else '',
                         f'https://example.org/tender/{number}', 'unknown'])
    return output.getvalue().encode()


class ScreeningModel:
    def __init__(self, *, fail_on=None):
        self.requests = []
        self.fail_on = fail_on

    def generate(self, request_id, prompt, **kwargs):
        data = json.loads(prompt.split('\n', 1)[1])
        self.requests.append((request_id, data))
        items = [dict(id=row['id'], fit='irrelevant', reason='Товарное соответствие отсутствует.',
                      evidence_indexes=[], risks=[], next_steps=[], product_group=None,
                      tender_quote=None, profile_quote=None,positions=[],positions_complete=False) for row in data['TENDERS']]
        if self.fail_on == len(self.requests):
            items = []
        return {'model': 'screening-test', 'answer': json.dumps({'items': items}, ensure_ascii=False)}


def enqueue(service, artifacts, actor, count, profile=b'product\nBolt\n', profile_name='profile.csv'):
    batch = service.import_batch(actor, add(artifacts, actor, 'all.csv', procurement_csv(count)), 'import-all')
    ref = add(artifacts, actor, profile_name, profile)
    run = service.start(actor, UUID(batch['batch_id']), [ref], '', 'screen-all')
    return batch, run


def test_only_unexpired_candidates_reach_model_once_and_every_input_stays_in_report(radar):
    service, artifacts, actor, repository, _ = radar
    model = service.model = ScreeningModel()
    batch, run = enqueue(service, artifacts, actor, 53)
    expected_ids = [row['id'] for row in repository.batch(actor, UUID(batch['batch_id']))['data']['records'] if row['deadline_at'] is None]
    assert service.run_once()
    result = service.read(actor, UUID(run['run_id']))
    assert result['state'] == 'completed', result['error']
    assert result['report']['counts'] == dict(total=53, model_analysed=27, take_in_work=0,
                                            needs_review=0, excluded=53, not_analysed=0)
    assert [row['id'] for _, data in model.requests for row in data['TENDERS']] == expected_ids
    assert len(model.requests) > 1
    assert len({request_id for request_id, _ in model.requests}) == len(model.requests)
    assert all(item['fit']==('irrelevant' if item['id'] in expected_ids else None) for item in result['report']['items'])
    assert service.read(actor, UUID(run['run_id'])) == result
    assert not service.run_once()


def test_later_invalid_portion_fails_whole_run_without_partial_publication_or_retry(radar):
    service, artifacts, actor, _, _ = radar
    model = service.model = ScreeningModel(fail_on=2)
    _, run = enqueue(service, artifacts, actor, 53)
    assert service.run_once()
    result = service.read(actor, UUID(run['run_id']))
    assert result['state'] == 'failed'
    assert result['report'] is None
    assert len(model.requests) == 2
    assert not service.run_once()
    assert len(model.requests) == 2


def test_profile_is_read_completely_without_query_selection_or_fragment_limit(radar):
    service, artifacts, actor, _, _ = radar
    model = service.model = ScreeningModel()
    profile = ('product,description\n' + ''.join(f'GROUP_{i:03},{"x" * 200}\n' for i in range(150))).encode()
    _, run = enqueue(service, artifacts, actor, 2, profile)
    service.run_once()
    result = service.read(actor, UUID(run['run_id']))
    assert result['state'] == 'completed', result['error']
    assert model.requests, 'Every tender must reach the model even without lexical overlap'
    excerpts = ''.join(source['excerpt'] for source in model.requests[0][1]['SELECTED_SOURCES'])
    assert 'GROUP_000' in excerpts and 'GROUP_149' in excerpts
    assert all(not source['truncated'] for source in result['report']['sources'])


@pytest.mark.parametrize(('profile', 'profile_name'), [
    (b'Bolt ' + b'x' * 5000001, 'profile.txt'),
    (('product,description\n' + ''.join(f'GROUP_{i:03},{"x" * 500}\n' for i in range(240))).encode(), 'profile.csv'),
], ids=['reader-truncated-text', 'profile-over-budget'])
def test_profile_that_cannot_fit_completely_is_rejected_before_model_calls(radar, profile, profile_name):
    service, artifacts, actor, _, _ = radar
    model = service.model = ScreeningModel()
    _, run = enqueue(service, artifacts, actor, 2, profile, profile_name)
    service.run_once()
    result = service.read(actor, UUID(run['run_id']))
    assert result['state'] == 'failed' and result['report'] is None
    assert model.requests == []


def test_http_returns_product_fit_and_verbatim_evidence_without_breaking_old_reports(radar, pg_engine, tmp_path):
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from bervel.modules.tender_radar.api import ReportResponse
    from http_client import ASGITestClient
    from test_tender_importer import normalized
    service, artifacts, actor, _, _ = radar
    batch = service.import_batch(actor, add(artifacts, actor, 't.csv', normalized()), 'i')
    ref = add(artifacts, actor, 'p.txt', 'Болт М16 ГОСТ7798'.encode())
    run = service.start(actor, UUID(batch['batch_id']), [ref], '', 's')
    service.run_once()
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), tmp_path / 'objects', {'a' * 32: actor})
    with ASGITestClient(create_app(settings)) as http:
        response = http.get('/api/v0.1/radar/runs/' + run['run_id'], headers={'Authorization': 'Bearer ' + 'a' * 32})
    assert response.status_code == 200
    report = response.json()['report']
    assert report['schema_version'] == '1.2.0'
    assert report['items'][0]['fit'] == 'relevant'
    assert report['items'][0]['profile_quote'] == 'Болт'
    old = dict(report, schema_version='1.0.0', items=[{k: v for k, v in report['items'][0].items()
                                                    if k not in {'fit', 'product_group', 'tender_quote', 'profile_quote'}}])
    restored = ReportResponse.model_validate(old).model_dump()
    assert 'fit' in restored['items'][0] and restored['items'][0]['fit'] is None


def test_complete_profile_rechecks_each_selected_file_even_when_both_indexes_are_cached(radar, services):
    service, artifacts, actor, _, _ = radar
    model = service.model = ScreeningModel()
    batch = service.import_batch(actor, add(artifacts, actor, 't.csv', procurement_csv(1)), 'i')
    first = add(artifacts, actor, 'p.txt', b'Bolt original file')
    second = add(artifacts, actor, 'p.txt', b'Bolt separate new file')
    run = service.start(actor, UUID(batch['batch_id']), [first, second], '', 's')
    service.memory.context(actor, run['sources_snapshot'], '', complete=True)
    metadata = artifacts.metadata(actor, second)
    digest = metadata['object_ref'].split(':')[1]
    (services[2].root / digest[:2] / digest).write_bytes(b'tampered')
    service.run_once()
    result = service.read(actor, UUID(run['run_id']))
    assert result['state'] == 'failed' and result['report'] is None
    assert model.requests == []


@pytest.mark.parametrize(('image_count', 'chunk_count', 'state'), [(1, 3, 'completed'), (2, 1, 'completed'), (3, 1, 'failed')])
def test_full_visual_profile_requires_an_attachment_for_every_selected_image(radar, image_count, chunk_count, state):
    import base64
    service, artifacts, actor, _, _ = radar
    model = service.model = ScreeningModel()
    batch = service.import_batch(actor, add(artifacts, actor, 't.csv', procurement_csv(1)), 'i')
    pixel = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5eQAAAAASUVORK5CYII=')
    refs = [add(artifacts, actor, f'profile-{i}.png', pixel) for i in range(image_count)]
    # Canonical cached OCR result avoids running the external OCR process;
    # original access, index lookup and image attachment limits remain real.
    for ref in refs:
        service.memory.repository.save_document_index(artifacts.metadata(actor, ref), {
            'status': 'ready', 'chunks': [dict(row_start=1, row_end=1, total_rows=1, unit='image',
                excerpt='Изображение: распознаваемый текст отсутствует. Визуальное содержимое требует просмотра модели.') for _ in range(chunk_count)]})
    run = service.start(actor, UUID(batch['batch_id']), refs, '', 's')
    service.run_once()
    result = service.read(actor, UUID(run['run_id']))
    assert result['state'] == state
    if state == 'failed':
        assert result['report'] is None and model.requests == []
    else:
        assert result['report']['counts']['model_analysed'] == 1
