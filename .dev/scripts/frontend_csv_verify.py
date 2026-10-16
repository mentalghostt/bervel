"""Exercise the browser/API/SQL chain inside an isolated schema of the test DB."""
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

from sqlalchemy import create_engine, text
from bervel.infrastructure.database import migrate

ROOT = Path(__file__).resolve().parents[2]


def wait_ready(url, process):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('Verification server stopped; inspect its project-local log')
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError('Verification server did not become ready')


def main():
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node.js is required')
    for port in (8012, 5174):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', port))
    run_id = uuid4().hex
    schema = 'ui_' + run_id
    run_root = ROOT / '.dev/local/frontend/verification' / run_id
    run_root.mkdir(parents=True)
    temporary = run_root / 'tmp'
    temporary.mkdir()
    report_path = run_root / 'live-results.json'
    admin = create_engine(os.environ.get('BERVEL_TEST_DATABASE_URL',
        'postgresql+psycopg://bervel_core_stage31@127.0.1.49:5432/bervel_core_stage31_test'), hide_parameters=True)
    if admin.url.database != 'bervel_core_stage31_test':
        raise RuntimeError('Only the dedicated bervel_core_stage31_test database is allowed')
    processes = []
    schema_created = False
    engine = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
        schema_created = True
        engine = create_engine(admin.url.update_query_dict({'options': f'-csearch_path={schema}'}), hide_parameters=True)
        migrate(engine)
        env = os.environ.copy()
        key = secrets.token_hex(32)
        env.update({
            'BERVEL_DATABASE_URL': engine.url.render_as_string(hide_password=False),
            'BERVEL_OBJECT_ROOT': str(run_root / 'objects'),
            'BERVEL_ACCESS_KEYS': json.dumps([{'key': key, 'actor_id': 'ui-browser-test', 'data_scope': schema,
                'permissions': ['artifact:write', 'artifact:read', 'dataset:publish', 'dataset:read']}]),
            'BERVEL_HOST': '127.0.0.1', 'BERVEL_PORT': '8012',
            'BERVEL_API_TARGET': 'http://127.0.0.1:8012',
            'BERVEL_FRONTEND_URL': 'http://127.0.0.1:5174',
            'BERVEL_E2E_ACCESS_KEY': key, 'BERVEL_E2E_REPORT': str(report_path),
            'TEMP': str(temporary), 'TMP': str(temporary), 'PYTHONDONTWRITEBYTECODE': '1',
            'npm_config_cache': str(ROOT / '.dev/local/frontend/npm-cache'),
        })
        env.setdefault('NODE_PATH', str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'))
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with (run_root / 'api.log').open('w', encoding='utf-8') as api_log, (run_root / 'vite.log').open('w', encoding='utf-8') as vite_log:
            api = subprocess.Popen([sys.executable, '-m', 'bervel', 'serve'], cwd=ROOT, env=env,
                stdout=api_log, stderr=subprocess.STDOUT, creationflags=creationflags)
            processes.append(api)
            wait_ready('http://127.0.0.1:8012/health/ready', api)
            vite = subprocess.Popen([node, str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1',
                '--port', '5174', '--strictPort'], cwd=ROOT / 'frontend', env=env,
                stdout=vite_log, stderr=subprocess.STDOUT, creationflags=creationflags)
            processes.append(vite)
            wait_ready('http://127.0.0.1:5174', vite)
            # File-chooser/keyboard tests must not compete with other browser processes.
            completed = subprocess.run([node, '--test', '--test-concurrency=1', '.dev/tests/frontend-csv-selection.cjs',
                '.dev/tests/frontend-csv-upload.cjs', '.dev/tests/frontend-csv-live.cjs'], cwd=ROOT, env=env, timeout=120)
            if completed.returncode:
                raise RuntimeError('Browser checks failed')
            results = json.loads(report_path.read_text(encoding='utf-8'))
            if len(results) != 3:
                raise RuntimeError('Expected three completed live scenarios')
            checks = []
            with engine.connect() as connection:
                for result in results:
                    artifact_id = result['artifact']['artifact_id']
                    params = {'artifact': artifact_id}
                    metadata = connection.execute(text('SELECT metadata FROM artifact_revisions WHERE artifact_id = :artifact'), params).scalar_one()
                    assert metadata['media_type'] == 'text/csv'
                    count = connection.execute(text('SELECT count(*) FROM datasets WHERE artifact_id = :artifact'), params).scalar_one()
                    if result['dataset'] is None:
                        assert count == 0, 'Invalid CSV must not publish a dataset'
                        checks.append({'scenario': result['scenario'], 'datasets': 0, 'original_preserved': True})
                        continue
                    assert count == 1, 'Retries must not create another dataset'
                    ref = result['dataset']
                    rows = connection.execute(text('SELECT record FROM data_records WHERE dataset_id = :id AND revision = :revision ORDER BY ordinal'),
                        {'id': ref['dataset_id'], 'revision': ref['revision']}).scalars().all()
                    assert len(rows) == 2
                    assert [row['payload']['quantity']['value'] for row in rows] == ['10', None]
                    assert [row['payload']['quantity']['state'] for row in rows] == ['KNOWN', 'UNKNOWN']
                    checks.append({'scenario': result['scenario'], 'datasets': count, 'rows': len(rows), 'values': ['10', None]})
            (run_root / 'verification.json').write_text(json.dumps({'ok': True, 'database': admin.url.database, 'checks': checks},
                ensure_ascii=False, indent=2), encoding='utf-8')
            print('Browser/API/PostgreSQL verification passed. Report:', run_root / 'verification.json')
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    process.terminate()
                process.wait(timeout=10)
        if engine is not None:
            engine.dispose()
        if schema_created:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Frontend verification failed; inspect project-local logs in .dev/local/frontend/verification.', file=sys.stderr)
        raise SystemExit(1)
