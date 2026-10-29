"""Verify a real persistent queue against the isolated Docker test API/worker."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request
from uuid import uuid4

from sqlalchemy.engine import make_url
from sqlalchemy import create_engine, text

assert make_url(os.environ['BERVEL_TEST_DATABASE_URL']).database == 'bervel_core_stage31_test'
root = Path(__file__).resolve().parents[2]
report_path = root / '.dev/local/core-stage-3.1/python-background-live.json'
key = os.environ['BERVEL_E2E_ACCESS_KEY']
parser = argparse.ArgumentParser()
parser.add_argument('phase', choices=['enqueue', 'verify'])
args = parser.parse_args()


def request(path, body=None, *, data=None, content_type='application/json'):
    headers = {'Authorization': 'Bearer ' + key}
    if body is not None:
        data = json.dumps(body).encode()
    if data is not None:
        headers['Content-Type'] = content_type
    req = Request('http://api-e2e:8011/api/v0.1' + path, data=data, headers=headers)
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


if args.phase == 'enqueue':
    marker = uuid4().hex
    boundary = 'bervel' + marker
    csv = (root / '.dev/fixtures/core-stage-3.1/requirements-v1.csv').read_bytes()
    data = (f'--{boundary}\r\nContent-Disposition: form-data; name="command_key"\r\n\r\n{marker}-upload\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="python-queue-{marker}.csv"\r\n'
            'Content-Type: text/csv\r\n\r\n').encode() + csv + f'\r\n--{boundary}--\r\n'.encode()
    status, artifact = request('/artifacts', data=data, content_type='multipart/form-data; boundary=' + boundary)
    assert status == 200, artifact
    command = {'artifact_id': artifact['artifact_id'], 'artifact_revision': artifact['revision'], 'command_key': marker + '-process'}
    path = '/demo/document-review/processes'
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: request(path, command), range(2)))
    assert all(status == 202 for status, _ in results)
    assert len({run['process_run_id'] for _, run in results}) == 1
    run = results[0][1]
    assert run['state'] == 'QUEUED', 'This phase requires the test worker to be stopped'
    assert request(path, {**command, 'artifact_revision': command['artifact_revision'] + 1})[0] == 409
    report_path.write_text(json.dumps({'command': command, 'process_run_id': run['process_run_id'],
        'queued_with_worker_stopped': True, 'concurrent_start_deduplicated': True, 'changed_command_rejected': True}, indent=2))
    print('Persistent test job queued; concurrent duplicate and conflict checks passed.')
else:
    report = json.loads(report_path.read_text())
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status, run = request('/processes/' + report['process_run_id'])
        assert status == 200, run
        if run['state'] == 'COMPLETED':
            break
        time.sleep(0.2)
    else:
        raise AssertionError('Python worker did not finish the preserved queued job')
    status, repeated = request('/demo/document-review/processes', report['command'])
    assert status == 202 and repeated['result'] == run['result']
    ref = run['result']
    status, records = request(f"/datasets/{ref['dataset_id']}/revisions/{ref['revision']}/records")
    assert status == 200 and len(records['records']) == 2
    assert len(run['attempts']) == 1
    engine = create_engine(os.environ['BERVEL_TEST_DATABASE_URL'])
    try:
        with engine.connect() as connection:
            published = connection.execute(text('SELECT count(*) FROM dataset_revisions WHERE process_run_id=:id'),
                                           {'id': report['process_run_id']}).scalar_one()
            assert published == 1
    finally:
        engine.dispose()
    report.update(completed_after_worker_start=True, result=ref, record_count=2,
                  published_dataset_revisions=published, replay_returned_same_result=True)
    report_path.write_text(json.dumps(report, indent=2))
    print('Real Python worker completed the persisted job; replay returned the same two rows.')
