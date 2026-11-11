"""Check a clean PostgreSQL initialization without retired service settings."""
import json
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid4

root = Path(__file__).resolve().parents[2]
docker = shutil.which('docker') or r'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
name = 'bervel-python-init-check-' + uuid4().hex
image = 'postgres:17.9-bookworm@sha256:47f917f7409eacd22fc5dfb1dee634e1b55cf0c01d1a7eb701be2227a03e0641'


def run(*args):
    return subprocess.check_output([docker, *args], cwd=root, text=True, encoding='utf-8', stderr=subprocess.PIPE)


created = False
try:
    run('run', '-d', '--rm', '--name', name, '--tmpfs', '/var/lib/postgresql/data',
        '-e', 'POSTGRES_PASSWORD=isolated-init-test-only', '-e', 'BERVEL_DB_PASSWORD=isolated-app-test-only',
        '-e', 'BERVEL_TEST_DB_PASSWORD=isolated-tests-only',
        '--mount', 'type=bind,source=' + str(root / 'docker/postgres/01-databases.sh') + ',target=/docker-entrypoint-initdb.d/01-databases.sh,readonly',
        image)
    created = True
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        ready = subprocess.run([docker, 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres'],
                               capture_output=True, timeout=5)
        if ready.returncode == 0:
            break
        time.sleep(0.3)
    else:
        raise AssertionError('Clean PostgreSQL initialization failed')
    databases = run('exec', name, 'psql', '-U', 'postgres', '-d', 'postgres', '-Atc',
                    'SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname').splitlines()
    assert databases == ['bervel_core_stage31', 'bervel_core_stage31_test', 'postgres'], databases
    report = {'fresh_postgres_initialization': True, 'application_and_test_databases': True,
              'retired_service_credentials_required': False}
    (root / '.dev/local/python-background/fresh-init.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
finally:
    if created:
        run('container', 'rm', '--force', name)
