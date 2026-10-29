"""Compare the pre-change backup with current SQL and read every old original/result."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from urllib.request import build_opener, ProxyHandler, Request
from uuid import uuid4

root = Path(__file__).resolve().parents[2]
backup = root / '.dev/local/docker/backups/docker-20261022T091022Z'
docker = shutil.which('docker') or r'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
database = 'bervel_preserve_check_' + uuid4().hex
dump = '/tmp/' + database + '.dump'
compose = [docker, 'compose']


def run(*args):
    return subprocess.check_output([*compose, *args], cwd=root, text=True, encoding='utf-8', stderr=subprocess.PIPE)


def rows(name, table):
    # The retired callback hash is technical authentication material, not an input/result.
    sql = 'SELECT to_jsonb(t)' + (" - 'invocation_hash'" if table == 'process_jobs' else '') + ' FROM ' + table + ' t'
    output = run('exec', '-T', '-e', 'PGOPTIONS=-c timezone=UTC', 'postgres', 'psql', '-U', 'postgres', '-d', name, '-Atc', sql)
    return [json.loads(line) for line in output.splitlines() if line]


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8-sig'))
for file in manifest['files']:
    assert Path(file['name']).name == file['name']
    assert hashlib.sha256((backup / file['name']).read_bytes()).hexdigest() == file['sha256']

settings = dict(line.split('=', 1) for line in (root / '.env').read_text(encoding='utf-8').splitlines()
                if '=' in line and not line.startswith('#'))
bindings = json.loads(settings['BERVEL_ACCESS_KEYS'].strip("'\""))
keys = {item['data_scope']: item['key'] for item in bindings
        if {'artifact:read', 'dataset:read'} <= set(item['permissions'])}
opener = build_opener(ProxyHandler({}))


def get(path, scope):
    request = Request('http://127.0.0.1:8011/api/v0.1' + path, headers={'Authorization': 'Bearer ' + keys[scope]})
    with opener.open(request, timeout=10) as response:
        return response.read()


created = False
try:
    run('exec', '-T', 'postgres', 'createdb', '-U', 'postgres', database)
    created = True
    run('cp', str(backup / 'bervel_core_stage31.dump'), 'postgres:' + dump)
    run('exec', '-T', 'postgres', 'pg_restore', '-U', 'postgres', '--no-owner', '--no-acl',
        '--single-transaction', '--exit-on-error', '--dbname=' + database, dump)
    tables = ('artifacts', 'artifact_revisions', 'schema_definitions', 'command_receipts', 'audit', 'datasets',
              'input_snapshots', 'process_runs', 'dataset_revisions', 'data_records', 'process_jobs', 'process_attempts')
    original = {table: rows(database, table) for table in tables}
    counts = {}
    for table in tables:
        current = {canonical(row) for row in rows('bervel_core_stage31', table)}
        assert {canonical(row) for row in original[table]} <= current, 'Historical rows changed in ' + table
        counts[table] = len(original[table])
    scopes = {row['artifact_id']: row['data_scope'] for row in original['artifacts']}
    for row in original['artifact_revisions']:
        content = get(f"/artifacts/{row['artifact_id']}/revisions/{row['revision']}/content", scopes[row['artifact_id']])
        assert hashlib.sha256(content).hexdigest() == row['metadata']['sha256']
        assert len(content) == row['metadata']['size']
    for row in original['dataset_revisions']:
        expected = [record['record'] for record in sorted(original['data_records'], key=lambda record: record['ordinal'])
                    if record['dataset_id'] == row['dataset_id'] and record['revision'] == row['revision']]
        result, cursor = [], 0
        while cursor is not None:
            page = json.loads(get(f"/datasets/{row['dataset_id']}/revisions/{row['revision']}/records?limit=100&cursor={cursor}",
                                  scopes[row['artifact_id']]))
            result.extend(page['records'])
            cursor = page['next_cursor']
        assert result == expected, 'Saved dataset records changed'
    report = {'preserved_rows': counts, 'original_revisions_read': len(original['artifact_revisions']),
              'dataset_revisions_read': len(original['dataset_revisions']), 'all_record_values_match': True,
              'backup_checksums_match': True}
    (root / '.dev/local/python-background/preservation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
finally:
    if created:
        run('exec', '-T', 'postgres', 'dropdb', '-U', 'postgres', database)
    run('exec', '-T', 'postgres', 'rm', '-f', dump)
