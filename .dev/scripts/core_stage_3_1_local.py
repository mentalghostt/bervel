"""Local launch and live HTTP verification; controls only its own API process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
LOCAL = ROOT / '.dev/local/core-stage-3.1'
FIXTURES = ROOT / '.dev/fixtures/core-stage-3.1'


def local_environment() -> dict[str, str]:
    env = os.environ.copy()
    temporary = LOCAL / 'tmp'
    temporary.mkdir(parents=True, exist_ok=True)
    env.update(TEMP=str(temporary), TMP=str(temporary), PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    return env


def check(base: str) -> dict:
    bindings = json.loads(os.environ['BERVEL_ACCESS_KEYS'])
    key_a = next(b['key'] for b in bindings if b['data_scope'] == 'test-area-a' and 'dataset:publish' in b['permissions'])
    key_b = next(b['key'] for b in bindings if b['data_scope'] == 'test-area-b')
    prefix = uuid4().hex
    with httpx.Client(base_url=base, timeout=20, trust_env=False, headers={'Authorization': 'Bearer ' + key_a}) as client:
        def get(path):
            response = client.get('/api/v0.1' + path)
            assert response.status_code == 200, f'GET failed: {response.status_code}'
            return response.json()
        def upload(content, name, key, path='/artifacts', expected=None):
            data = {'command_key': prefix + key, 'origin_system': 'manual-test'}
            if expected is not None:
                data['expected_revision'] = str(expected)
            response = client.post('/api/v0.1' + path, data=data, files={'file': (name, content, 'text/csv')})
            assert response.status_code == 200, f'Upload failed: {response.status_code}'
            return response.json()
        def extract(body):
            response = client.post('/api/v0.1/demo/document-review/extractions', json=body)
            assert response.status_code == 200, f'Extraction failed: {response.status_code}'
            return response.json()
        original1 = (FIXTURES / 'requirements-v1.csv').read_bytes()
        original2 = (FIXTURES / 'requirements-v2.csv').read_bytes()
        first = upload(original1, 'requirements-v1.csv', 'upload-v1')
        assert upload(original1, 'requirements-v1.csv', 'upload-v1') == first
        content_path = f"/artifacts/{first['artifact_id']}/revisions/1/content"
        assert client.get('/api/v0.1' + content_path).content == original1
        body1 = {'artifact_id': first['artifact_id'], 'artifact_revision': 1, 'command_key': prefix + 'extract-v1',
                 'module_version': '1.0.0', 'expected_revision': 0}
        result1 = extract(body1)
        assert extract(body1) == result1
        dataset_path = f"/datasets/{result1['dataset_id']}/revisions/1"
        assert get(dataset_path)['quality_state'] == 'PARTIAL'
        first_records = get(dataset_path + '/records')['records']
        assert [r['payload']['quantity']['value'] for r in first_records] == ['10', None]
        assert get(dataset_path + '/records?limit=1')['next_cursor'] == 1
        second = upload(original2, 'requirements-v2.csv', 'upload-v2', f"/artifacts/{first['artifact_id']}/revisions", 1)
        result2 = extract({**body1, 'artifact_revision': 2, 'command_key': prefix + 'extract-v2',
                           'dataset_id': result1['dataset_id'], 'expected_revision': 1})
        assert result2['revision'] == second['revision'] == 2
        assert get(f"/datasets/{result2['dataset_id']}/revisions/2/records")['records'][1]['payload']['quantity']['value'] == '4'
        assert get(dataset_path + '/records')['records'] == first_records
        conflict = client.post('/api/v0.1/demo/document-review/extractions', json={**body1, 'artifact_revision': 2})
        assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
        foreign_paths = [content_path, f"/artifacts/{first['artifact_id']}/revisions/1", dataset_path,
                         dataset_path + '/records', f"/artifacts/{first['artifact_id']}/history"]
        for path in foreign_paths:
            assert client.get('/api/v0.1' + path, headers={'Authorization': 'Bearer ' + key_b}).status_code == 404
        assert client.get('/api/v0.1' + content_path, headers={'Authorization': 'Bearer unknown'}).status_code == 401
        invalid = upload(b'line,name,quantity,unit\n1,A,NaN,pcs\n', 'invalid.csv', 'invalid')
        rejected = client.post('/api/v0.1/demo/document-review/extractions', json={
            'artifact_id': invalid['artifact_id'], 'artifact_revision': 1, 'command_key': prefix + 'invalid-extract'})
        assert rejected.status_code == 422
        assert get(f"/artifacts/{invalid['artifact_id']}/history")['datasets'] == []
        history = get(f"/artifacts/{first['artifact_id']}/history")
        assert len(history['revisions']) == len(history['datasets']) == 2
        return {'ok': True, 'artifact': first, 'dataset': result1,
                'sha256_v1': hashlib.sha256(original1).hexdigest(), 'values_v1': ['10', None],
                'values_v2': ['10', '4'], 'quality_v1': 'PARTIAL', 'quality_v2': 'COMPLETE',
                'repeats': 'same refs', 'changed_key_input': 409, 'foreign_scope': 404, 'unknown_key': 401,
                'invalid_csv': 'original preserved; dataset absent'}


def start_api(env: dict, base: str):
    log = (LOCAL / 'api.log').open('ab')
    process = subprocess.Popen([sys.executable, '-m', 'bervel', 'serve'], env=env, cwd=ROOT, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('API exited; inspect the local api.log')
            try:
                if httpx.get(base + '/health/ready', timeout=1, trust_env=False).status_code == 200:
                    return process, log
            except httpx.TransportError:
                pass
            time.sleep(0.2)
        raise RuntimeError('API did not become ready')
    except Exception:
        stop_api(process, log)
        raise


def stop_api(process, log):
    if process.poll() is None:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=True)
        else:
            process.terminate()
        process.wait(timeout=10)
    log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['serve', 'migrate', 'check', 'verify'])
    args = parser.parse_args()
    env = local_environment()
    base = 'http://' + env.get('BERVEL_HOST', '127.0.0.1') + ':' + env.get('BERVEL_PORT', '8011')
    if args.command in ('serve', 'migrate'):
        return subprocess.call([sys.executable, '-m', 'bervel', args.command], env=env, cwd=ROOT)
    if args.command == 'check':
        report = check(base)
    else:
        # Refuse to operate on an existing listener; every process stopped below was created here.
        import socket
        with socket.socket() as sock:
            if sock.connect_ex((env.get('BERVEL_HOST', '127.0.0.1'), int(env.get('BERVEL_PORT', '8011')))) == 0:
                raise RuntimeError('Port is occupied; choose a free BERVEL_PORT for verification')
        subprocess.run([sys.executable, '-m', 'bervel', 'migrate'], env=env, cwd=ROOT, check=True)
        process, log = start_api(env, base)
        try:
            report = check(base)
        finally:
            stop_api(process, log)
        process, log = start_api(env, base)
        try:
            key = next(b['key'] for b in json.loads(env['BERVEL_ACCESS_KEYS']) if b['data_scope'] == 'test-area-a')
            ref = report['dataset']
            with httpx.Client(base_url=base, headers={'Authorization': 'Bearer ' + key}, trust_env=False) as client:
                response = client.get(f"/api/v0.1/datasets/{ref['dataset_id']}/revisions/1/records")
                assert response.status_code == 200
                assert [r['payload']['quantity']['value'] for r in response.json()['records']] == ['10', None]
                original = report['artifact']
                restored = client.get(f"/api/v0.1/artifacts/{original['artifact_id']}/revisions/1/content")
                assert hashlib.sha256(restored.content).hexdigest() == report['sha256_v1']
            report['restart_persistence'] = True
        finally:
            stop_api(process, log)
    (LOCAL / ('live-' + args.command + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
