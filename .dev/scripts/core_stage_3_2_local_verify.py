"""One synthetic smoke process in the existing local environment; preserve it for inspection."""
import hashlib
import json
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from stage32_bootstrap import ROOT, read_env

ENV = read_env(ROOT / '.env')
BINDING = next(binding for binding in json.loads(ENV['BERVEL_ACCESS_KEYS']) if 'process:start' in binding['permissions'])
API = 'http://127.0.0.1:8011'


def request(path, payload=None, *, data=None, content_type='application/json', expected=200, worker=False):
    body = data if data is not None else json.dumps(payload).encode() if payload is not None else None
    key = ENV['BERVEL_PROCESS_WORKER_KEY'] if worker else BINDING['key']
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': content_type}
    try:
        with urlopen(Request(API + path, data=body, headers=headers), timeout=30) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    if status != expected:
        raise AssertionError('Unexpected HTTP ' + str(status) + ' at ' + path)
    return raw


def main():
    original = (ROOT / '.dev/fixtures/core-stage-3.1/requirements-v1.csv').read_bytes()
    boundary = 'stage32-' + uuid4().hex
    body = b''
    for name, value in [('command_key', uuid4().hex), ('origin_system', 'core-stage-3.2-verification')]:
        body += ('--' + boundary + '\r\nContent-Disposition: form-data; name="' + name + '"\r\n\r\n' + value + '\r\n').encode()
    body += ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="core-stage-3.2-local.csv"\r\nContent-Type: text/csv\r\n\r\n').encode()
    body += original + ('\r\n--' + boundary + '--\r\n').encode()
    artifact = json.loads(request('/api/v0.1/artifacts', data=body, content_type='multipart/form-data; boundary=' + boundary))
    command = {'artifact_id': artifact['artifact_id'], 'artifact_revision': 1, 'command_key': uuid4().hex}
    process = json.loads(request('/api/v0.1/demo/document-review/processes', command, expected=202))
    run_id = process['process_run_id']
    assert json.loads(request('/api/v0.1/demo/document-review/processes', command, expected=202))['process_run_id'] == run_id
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        process = json.loads(request('/api/v0.1/processes/' + run_id))
        if process['state'] == 'COMPLETED':
            break
        if process['state'] == 'FAILED':
            raise AssertionError('Local process failed: ' + str(process['attempts'][-1]['error_code']))
        time.sleep(0.25)
    assert process['state'] == 'COMPLETED', process['state']
    result = process['result']
    records = json.loads(request('/api/v0.1/datasets/' + result['dataset_id'] + '/revisions/1/records'))['records']
    assert [row['payload']['quantity']['value'] for row in records] == ['10', None]
    downloaded = request('/api/v0.1/artifacts/' + artifact['artifact_id'] + '/revisions/1/content')
    assert downloaded == original
    request('/api/v0.1/processes/' + run_id, expected=401, worker=True)
    with urlopen('http://127.0.0.1:5173/', timeout=5) as response:
        assert response.status == 200
    sql = "SELECT json_build_object('results',(SELECT count(*) FROM dataset_revisions WHERE process_run_id='" + run_id + "')," + \
          "'records',(SELECT count(*) FROM data_records r JOIN dataset_revisions d USING(dataset_id,revision) WHERE d.process_run_id='" + run_id + "'));"
    completed = subprocess.run(['docker', 'compose', '-f', 'compose.yaml', '-f', 'compose.orchestration.yaml',
        'exec', '-T', 'postgres', 'psql', '-U', 'postgres', '-d', 'bervel_core_stage31', '-At', '-c', sql],
        cwd=ROOT, capture_output=True, text=True, check=True)
    counts = json.loads(completed.stdout)
    assert counts == {'results': 1, 'records': 2}
    proof = {'status': 'passed', 'process_run_id': run_id, 'artifact': artifact, 'result': result,
             'execution_id': process['attempts'][0]['execution_id'], 'workflow': process['workflow'],
             'original_sha256': hashlib.sha256(downloaded).hexdigest(), 'quantities': ['10', None],
             'worker_key_rejected_by_user_api': True, 'frontend_http': 200, 'sql': counts}
    (ROOT / '.dev/local/core-stage-3.2/local-smoke.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
