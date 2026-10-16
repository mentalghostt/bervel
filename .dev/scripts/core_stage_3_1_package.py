"""Probe only the installed wheel from an empty cwd, using stdlib HTTP."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError, URLError
from uuid import uuid4

from sqlalchemy import create_engine, text
import bervel

root = Path(os.environ.get('BERVEL_PROBE_ROOT', Path(__file__).resolve().parents[1] / 'local/core-stage-3.1/portable'))
root.mkdir(parents=True, exist_ok=True)
assert 'site-packages' in bervel.__file__ and '/src/' not in bervel.__file__.replace('\\', '/')
clean = root / 'clean-cwd'
clean.mkdir(exist_ok=True)
assert not (clean / '.dev').exists()
with socket.socket() as sock:
    if sock.connect_ex(('127.0.0.1', 8012)) == 0:
        raise RuntimeError('Port 8012 is occupied; package verification requires a free port')
admin = create_engine(os.environ['BERVEL_TEST_DATABASE_URL'])
assert admin.url.database == 'bervel_core_stage31_test'
schema = 'pack_' + uuid4().hex
with admin.begin() as connection:
    connection.execute(text(f'CREATE SCHEMA {schema}'))
env = os.environ.copy()
env.pop('PYTHONPATH', None)
env['BERVEL_DATABASE_URL'] = admin.url.update_query_dict({'options': f'-csearch_path={schema}'}).render_as_string(hide_password=False)
env['BERVEL_OBJECT_ROOT'] = str(root / 'objects')
env['BERVEL_PORT'] = '8012'
key_a, key_b = secrets.token_hex(32), secrets.token_hex(32)
permissions = ['artifact:read', 'artifact:write', 'dataset:read', 'dataset:publish', 'module:read']
env['BERVEL_ACCESS_KEYS'] = json.dumps([{'key': key_a, 'actor_id': 'portable-a', 'data_scope': 'test-area-a', 'permissions': permissions},
    {'key': key_b, 'actor_id': 'portable-b', 'data_scope': 'test-area-b', 'permissions': permissions}])
opener = build_opener(ProxyHandler({}))
base = 'http://127.0.0.1:8012'
process = None
log = (root / 'api.log').open('ab')


def request(path, data=None, content_type=None, key=key_a):
    headers = {'Authorization': 'Bearer ' + key}
    if content_type:
        headers['Content-Type'] = content_type
    req = Request(base + path, data=data, headers=headers)
    try:
        with opener.open(req, timeout=10) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def post_json(path, body):
    status, body = request('/api/v0.1' + path, json.dumps(body).encode(), 'application/json')
    assert status == 200, status
    return json.loads(body)


def upload(content, key, path='/artifacts', expected=None):
    boundary = uuid4().hex
    fields = {'command_key': key, 'origin_system': 'portable'}
    if expected is not None:
        fields['expected_revision'] = str(expected)
    body = b''
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fixture.csv"\r\nContent-Type: text/csv\r\n\r\n'.encode()
    body += content + f'\r\n--{boundary}--\r\n'.encode()
    status, result = request('/api/v0.1' + path, body, 'multipart/form-data; boundary=' + boundary)
    assert status == 200, status
    return json.loads(result)


try:
    for _ in range(2):
        subprocess.run([sys.executable, '-I', '-m', 'bervel', 'migrate'], env=env, cwd=clean, check=True)
    process = subprocess.Popen([sys.executable, '-I', '-m', 'bervel', 'serve'], env=env, cwd=clean, stdout=log, stderr=log)
    for _ in range(100):
        assert process.poll() is None, 'API exited'
        try:
            if request('/health/ready')[0] == 200:
                break
        except URLError:
            time.sleep(0.1)
    else:
        raise RuntimeError('API not ready')
    content1 = 'line,name,quantity,unit\n1,Test A,10,pcs\n2,Test B,,pcs\n'.encode()
    content2 = content1.replace(b'B,,pcs', b'B,4,pcs')
    ref1 = upload(content1, 'portable-upload-1')
    assert upload(content1, 'portable-upload-1') == ref1
    body1 = {'artifact_id': ref1['artifact_id'], 'artifact_revision': 1, 'command_key': 'portable-extract-1'}
    ds1 = post_json('/demo/document-review/extractions', body1)
    assert post_json('/demo/document-review/extractions', body1) == ds1
    upload(content2, 'portable-upload-2', f"/artifacts/{ref1['artifact_id']}/revisions", 1)
    ds2 = post_json('/demo/document-review/extractions', {**body1, 'artifact_revision': 2, 'command_key': 'portable-extract-2',
        'dataset_id': ds1['dataset_id'], 'expected_revision': 1})
    for revision, expected in [(1, None), (2, '4')]:
        status, body = request(f"/api/v0.1/datasets/{ds1['dataset_id']}/revisions/{revision}/records")
        assert status == 200
        assert json.loads(body)['records'][1]['payload']['quantity']['value'] == expected
    path = f"/api/v0.1/artifacts/{ref1['artifact_id']}/revisions/1/content"
    assert hashlib.sha256(request(path)[1]).digest() == hashlib.sha256(content1).digest()
    assert request(path, key=key_b)[0] == 404
    assert request('/docs')[0] == 200
    report = {'ok': True, 'wheel_only': True, 'empty_cwd': True, 'source_dev_not_required': True,
              'migration_repeat': True, 'two_revisions': True, 'repeat': True, 'foreign_scope': 404}
    (root / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
finally:
    if process and process.poll() is None:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, check=True)
        process.wait(timeout=10)
    log.close()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    admin.dispose()
