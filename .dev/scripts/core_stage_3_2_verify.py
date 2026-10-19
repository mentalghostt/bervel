"""Real queue/recovery checks against the dedicated bervel-stage32 Docker project."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from stage32_bootstrap import ROOT, read_env

sys.path.insert(0, str(ROOT / 'src'))
from bervel.infrastructure.n8n import N8nEngine
from bervel.infrastructure.n8n_setup import build_workflow

DIRECTORY = ROOT / '.dev/local/core-stage-3.2'
ENV = read_env(DIRECTORY / 'test.env')
ENGINE = N8nEngine('http://127.0.0.1:15688', ENV['BERVEL_N8N_API_KEY'], ENV['BERVEL_N8N_WEBHOOK_KEY'],
                   ENV['BERVEL_N8N_WORKFLOW_ID'], 'bervel-document-review-v1', timeout=30)
API, GATE = 'http://127.0.0.1:18011', 'http://127.0.0.1:15699'
COMPOSE = ['docker', 'compose', '--env-file', '.dev/local/core-stage-3.2/test.env', '-f', '.dev/docker/compose.stage32.yaml']
RESULT = {'profile': 'core-stage-3.2', 'synthetic_data_only': True, 'isolated_project': 'bervel-stage32', 'checks': []}


def request(url, payload=None, key=None, expected=200, *, raw=None, content_type='application/json'):
    data = raw if raw is not None else json.dumps(payload).encode() if payload is not None else None
    headers = {'Content-Type': content_type}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=45) as response:
            status, body = response.status, response.read()
    except HTTPError as error:
        status, body = error.code, error.read()
    value = json.loads(body) if body else {}
    if status != expected:
        code = value.get('error', {}).get('code', '') if isinstance(value, dict) else ''
        raise AssertionError('Unexpected HTTP ' + str(status) + ' (' + code + ') at ' + url.split('?', 1)[0])
    return value


def api(path, payload=None, expected=200, worker=False):
    return request(API + path, payload, ENV['BERVEL_PROCESS_WORKER_KEY'] if worker else ENV['STAGE32_USER_KEY'], expected)


def wait_for(condition, label, timeout=120):
    deadline, next_notice = time.monotonic() + timeout, time.monotonic() + 15
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        if time.monotonic() >= next_notice:
            print('Waiting: ' + label, flush=True)
            next_notice = time.monotonic() + 15
        time.sleep(0.5)
    raise TimeoutError(label)


def compose(*arguments):
    completed = subprocess.run(COMPOSE + list(arguments), cwd=ROOT, capture_output=True, text=True)
    with (DIRECTORY / 'integration-compose.log').open('a', encoding='utf-8') as log:
        log.write(completed.stdout + completed.stderr)
    if completed.returncode:
        raise RuntimeError('Isolated test Compose command failed; see integration-compose.log')
    return completed.stdout


def upload():
    boundary = 'stage32-' + uuid4().hex
    content = (ROOT / '.dev/fixtures/core-stage-3.1/requirements-v1.csv').read_bytes()
    raw = ('--' + boundary + '\r\nContent-Disposition: form-data; name="command_key"\r\n\r\n' + uuid4().hex +
           '\r\n--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="v1.csv"\r\nContent-Type: text/csv\r\n\r\n').encode()
    raw += content + ('\r\n--' + boundary + '--\r\n').encode()
    return request(API + '/api/v0.1/artifacts', key=ENV['STAGE32_USER_KEY'], raw=raw,
                   content_type='multipart/form-data; boundary=' + boundary)


def start():
    original = upload()
    body = {'artifact_id': original['artifact_id'], 'artifact_revision': 1, 'command_key': uuid4().hex}
    process = api('/api/v0.1/demo/document-review/processes', body, 202)
    return process['process_run_id'], body


def read(run):
    return api('/api/v0.1/processes/' + run)


def completed(run):
    def current():
        value = read(run)
        if value['state'] == 'FAILED':
            raise AssertionError('Process failed with ' + str(value['attempts'][-1]['error_code']))
        return value if value['state'] == 'COMPLETED' else None
    return wait_for(current, 'core completion')


def sql_counts(run):
    sql = "SELECT json_build_object('runs',(SELECT count(*) FROM process_runs WHERE process_run_id='" + run + "')," + \
          "'results',(SELECT count(*) FROM dataset_revisions WHERE process_run_id='" + run + "')," + \
          "'records',(SELECT count(*) FROM data_records r JOIN dataset_revisions d USING(dataset_id,revision) WHERE d.process_run_id='" + run + "'));"
    return json.loads(compose('exec', '-T', 'postgres', 'psql', '-U', 'postgres', '-d', 'bervel_stage32', '-At', '-c', sql).strip())


def check_result(run):
    process = completed(run)
    ref = process['result']
    records = api('/api/v0.1/datasets/' + ref['dataset_id'] + '/revisions/' + str(ref['revision']) + '/records')['records']
    assert [record['payload']['quantity']['value'] for record in records] == ['10', None]
    counts = sql_counts(run)
    assert counts == {'runs': 1, 'results': 1, 'records': 2}, counts
    return process, counts


def publish_gate(mode):
    previous = ENGINE.request('GET', '/api/v1/workflows/' + ENGINE.workflow_id)
    credentials = previous['nodes'][0]['credentials']['httpHeaderAuth']['id'], previous['nodes'][1]['credentials']['httpHeaderAuth']['id']
    workflow = build_workflow('http://api:8011', ENGINE.webhook_path, credentials[1], credentials[0])
    if mode == 'before':
        gate = {'id': 'test-gate', 'name': 'Test gate', 'type': 'n8n-nodes-base.httpRequest', 'typeVersion': 4.2,
            'position': [400, 180], 'parameters': {'method': 'POST', 'url': 'http://gate:5699/gate', 'sendBody': True,
                'specifyBody': 'json', 'jsonBody': "={{ JSON.stringify({process_run_id: $('Start').first().json.body.process_run_id, execution_id: $execution.id}) }}",
                'options': {'timeout': 120000}}}
        workflow['nodes'].append(gate)
        workflow['connections']['Claim'] = {'main': [[{'node': 'Test gate', 'type': 'main', 'index': 0}]]}
        workflow['connections']['Test gate'] = {'main': [[{'node': 'Execute', 'type': 'main', 'index': 0}]]}
    elif mode == 'after-commit':
        workflow['nodes'][-1]['parameters']['url'] = workflow['nodes'][-1]['parameters']['url'].replace('http://api:8011', 'http://gate:5699')
    updated = ENGINE.request('PUT', '/api/v1/workflows/' + ENGINE.workflow_id + '?publishIfActive=false', workflow)
    published = ENGINE.request('POST', '/api/v1/workflows/' + ENGINE.workflow_id + '/publish', {'versionId': updated['versionId']})
    return published.get('activeVersionId') or updated['versionId']


def gated(run):
    return next((item for item in request(GATE + '/')['events'] if item['process_run_id'] == run), None)


def crash_worker(identifier):
    compose('kill', '-s', 'SIGKILL', 'n8n-worker')
    compose('up', '-d', '--no-deps', '--wait', 'n8n-worker')
    wait_for(lambda: ENGINE.execution(identifier).state in {'crashed', 'error'}, 'n8n crash detection', timeout=180)


def normal():
    publish_gate('none')
    run, body = start()
    process, counts = check_result(run)
    assert api('/api/v0.1/demo/document-review/processes', body, 202)['process_run_id'] == run
    return {'process_run_id': run, 'execution_id': process['attempts'][0]['execution_id'], 'sql': counts,
            'workflow_version_id': process['workflow']['version_id']}


def crash_recover_original():
    pinned = publish_gate('before')
    request(GATE + '/control', {'hold': True})
    run, _ = start()
    entered = wait_for(lambda: gated(run), 'worker before publication')
    original = ENGINE.execution(entered['execution_id'])
    newer = publish_gate('none')
    assert newer != pinned
    crash_worker(original.execution_id)
    assert api('/api/v0.1/processes/' + run + '/reconcile', {})['state'] == 'FAILED'
    request(GATE + '/control', {'hold': False})
    command = {'command_key': uuid4().hex, 'expected_attempt': 1, 'reason': 'Synthetic test worker crash'}
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: api('/api/v0.1/processes/' + run + '/recover', command, 202), range(2)))
    assert all(reply['process_run_id'] == run for reply in replies)
    process, counts = check_result(run)
    assert process['active_attempt'] == 2
    child = ENGINE.execution(process['attempts'][-1]['execution_id'])
    assert child.retry_of == original.execution_id and child.version_id == pinned
    assert len(ENGINE.find(ENGINE.workflow_id, run, original.execution_id)) == 1
    api('/api/internal/v0.1/processes/' + run + '/execute',
        {'execution_id': original.execution_id, 'invocation_token': original.invocation_token}, 409, worker=True)
    assert sql_counts(run) == counts
    return {'process_run_id': run, 'parent_execution_id': original.execution_id, 'retry_execution_id': child.execution_id,
            'pinned_version': pinned, 'new_published_version': newer, 'retry_version': child.version_id,
            'attempts': len(process['attempts']), 'late_callback_http': 409, 'sql': counts}


def lost_response_after_commit():
    publish_gate('after-commit')
    request(GATE + '/control', {'hold': True})
    run, _ = start()
    entered = wait_for(lambda: gated(run), 'publication committed before worker received response')
    before, counts = check_result(run)
    original = ENGINE.execution(entered['execution_id'])
    crash_worker(original.execution_id)
    request(GATE + '/control', {'hold': False})
    after = api('/api/v0.1/processes/' + run + '/recover',
        {'command_key': uuid4().hex, 'expected_attempt': 1, 'reason': 'Reply lost after commit'}, 202)
    assert after['state'] == 'COMPLETED' and after['result'] == before['result'] and after['active_attempt'] == 1
    replay = api('/api/internal/v0.1/processes/' + run + '/execute',
        {'execution_id': original.execution_id, 'invocation_token': original.invocation_token}, worker=True)
    assert replay == before['result']
    assert sql_counts(run) == counts
    assert ENGINE.find(ENGINE.workflow_id, run, original.execution_id) == []
    publish_gate('none')
    return {'process_run_id': run, 'execution_id': original.execution_id, 'result_preserved': True,
            'retry_created': False, 'sql': counts}


def main():
    try:
        for name, operation in [('N01-real-queue-csv', normal), ('N02-crash-original-version-recovery', crash_recover_original),
                                ('N03-response-lost-after-commit', lost_response_after_commit)]:
            print('Running ' + name, flush=True)
            started = time.monotonic()
            detail = operation()
            RESULT['checks'].append({'id': name, 'status': 'passed', 'elapsed_seconds': round(time.monotonic() - started, 2),
                                     'details': detail})
            (DIRECTORY / 'integration-results.json').write_text(json.dumps(RESULT, indent=2), encoding='utf-8')
            print(name + ': passed', flush=True)
    except Exception as error:
        RESULT['failure'] = type(error).__name__ + ': ' + str(error)
        (DIRECTORY / 'integration-results.json').write_text(json.dumps(RESULT, indent=2), encoding='utf-8')
        print(RESULT['failure'], flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
