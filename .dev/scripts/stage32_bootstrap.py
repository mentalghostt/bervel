"""Development-only local n8n account/API-key bootstrap. Runtime uses public API."""
import argparse
import http.cookiejar
import json
from pathlib import Path
import secrets
import time
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]


def read_env(path):
    return {line.split('=', 1)[0]: line.split('=', 1)[1].strip().strip("'")
            for line in path.read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.startswith('#')}


def write_values(path, values):
    lines = path.read_text(encoding='utf-8-sig').splitlines() if path.exists() else []
    seen = set()
    for index, line in enumerate(lines):
        name = line.partition('=')[0]
        if name in values:
            value = values[name]
            lines[index] = name + '=' + ("'" + value + "'" if name == 'BERVEL_ACCESS_KEYS' else value)
            seen.add(name)
    for name, value in values.items():
        if name not in seen:
            lines.append(name + '=' + ("'" + value + "'" if name == 'BERVEL_ACCESS_KEYS' else value))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    path.chmod(0o600)


def initialize_test():
    path = ROOT / '.dev/local/core-stage-3.2/test.env'
    if path.exists():
        print('Existing stage32 test environment preserved.')
        return
    values = {name: secrets.token_hex(32) for name in ('STAGE32_DB_PASSWORD', 'N8N_ENCRYPTION_KEY', 'REDIS_PASSWORD',
        'BERVEL_PROCESS_WORKER_KEY', 'BERVEL_N8N_WEBHOOK_KEY', 'STAGE32_USER_KEY')}
    values['STAGE32_OWNER_PASSWORD'] = secrets.token_urlsafe(24) + 'Aa1!'
    permissions = ['module:read', 'artifact:read', 'artifact:write', 'dataset:read', 'dataset:publish',
                   'process:start', 'process:read', 'process:recover']
    values['BERVEL_ACCESS_KEYS'] = json.dumps([{'key': values['STAGE32_USER_KEY'], 'actor_id': 'stage32-operator',
        'data_scope': 'stage32-tests', 'permissions': permissions}], separators=(',', ':'))
    write_values(path, values)
    print('Created isolated stage32 test environment; secrets were not printed.')


def create_public_key(url, environment, local):
    values = read_env(environment)
    cookies = http.cookiejar.CookieJar()
    client = build_opener(HTTPCookieProcessor(cookies))
    def rest(path, payload=None):
        request = Request(url.rstrip('/') + '/rest/' + path, headers={'Content-Type': 'application/json'},
                          data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with client.open(request, timeout=30) as response:
                return json.loads(response.read())['data']
        except HTTPError as error:
            raise RuntimeError('Development bootstrap HTTP ' + str(error.code) + ' at /rest/' + path) from None
    if values.get('BERVEL_N8N_API_KEY'):
        print('Existing public API key preserved.')
        return
    if local:
        password = (ROOT / '.dev/local/core-stage-2/owner.env').read_text().split('=', 1)[1].strip()
        email = 'core-spike@example.invalid'
    else:
        password, email = values['STAGE32_OWNER_PASSWORD'], 'stage32-owner@example.invalid'
        try:
            rest('owner/setup', {'email': email, 'password': password, 'firstName': 'Stage32', 'lastName': 'Test'})
        except RuntimeError:
            pass  # Only this isolated development instance may already have its test owner.
    rest('login', {'emailOrLdapLoginId': email, 'password': password})
    scopes = rest('api-keys/scopes')
    created = rest('api-keys', {'label': 'BERVEL core 3.2 local integration', 'scopes': scopes,
                               'expiresAt': int(time.time()) + 30 * 86400})
    key = created.get('rawApiKey')
    if not key:
        raise RuntimeError('The development bootstrap did not receive a public API key')
    additions = {'BERVEL_N8N_API_KEY': key, 'BERVEL_N8N_URL': 'http://n8n-main:5688'}
    for name in ('BERVEL_PROCESS_WORKER_KEY', 'BERVEL_N8N_WEBHOOK_KEY'):
        additions[name] = values.get(name) or secrets.token_hex(32)
    if local:
        bindings = json.loads(values['BERVEL_ACCESS_KEYS'])
        for binding in bindings:
            if {'artifact:read', 'dataset:publish'} <= set(binding['permissions']):
                binding['permissions'] = sorted(set(binding['permissions']) | {'process:start', 'process:read', 'process:recover'})
        additions['BERVEL_ACCESS_KEYS'] = json.dumps(bindings, separators=(',', ':'))
    write_values(environment, additions)
    print('Saved local orchestration configuration; existing database/encryption keys preserved.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init-test', 'key-test', 'key-local', 'workflow-test', 'workflow-local'])
    args = parser.parse_args()
    local = args.action.endswith('local')
    environment = ROOT / ('.env' if local else '.dev/local/core-stage-3.2/test.env')
    if args.action == 'init-test':
        initialize_test()
    elif args.action.startswith('key'):
        create_public_key('http://127.0.0.1:' + ('5688' if local else '15688'), environment, local)
    else:
        import subprocess
        command = ['docker', 'compose'] + (['-f', 'compose.yaml', '-f', 'compose.orchestration.yaml'] if local else
            ['--env-file', '.dev/local/core-stage-3.2/test.env', '-f', '.dev/docker/compose.stage32.yaml'])
        completed = subprocess.run(command + ['run', '--rm', '--no-deps', 'api', 'python', '-m', 'bervel', 'provision-orchestration'],
            cwd=ROOT, capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError('Public API provisioning failed; inspect the application configuration and n8n availability')
        metadata = json.loads(completed.stdout.strip().splitlines()[-1])
        write_values(environment, {'BERVEL_N8N_WORKFLOW_ID': metadata['workflow_id']})
        (ROOT / '.dev/local/core-stage-3.2' / ('local-workflow.json' if local else 'test-workflow.json')).write_text(
            json.dumps(metadata, indent=2), encoding='utf-8')
        print('Published module workflow ' + metadata['workflow_id'] + '; no credentials were printed.')


if __name__ == '__main__':
    main()
