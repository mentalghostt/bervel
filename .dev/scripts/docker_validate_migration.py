"""Compare original PostgreSQL rows and read migrated originals/results through Docker API."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.request import Request, ProxyHandler, build_opener

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pg-bin', type=Path, required=True)
parser.add_argument('--after-recreate', action='store_true')
args = parser.parse_args()
docker = shutil.which('docker') or r'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
source_client = [str(args.pg_bin / 'psql.exe'), '-h', '127.0.1.49', '-U', 'postgres', '-d', 'bervel_core_stage31', '-w', '-qAt']
target_client = [docker, 'compose', 'exec', '-T', '-e', 'PGOPTIONS=-c timezone=UTC',
                 'postgres', 'psql', '-U', 'postgres', '-d', 'bervel_core_stage31', '-qAt']
env = {**os.environ, 'PGOPTIONS': '-c timezone=UTC'}


def rows(client, sql):
    return [json.loads(line) for line in subprocess.check_output([*client, '-c', sql], cwd=root, env=env,
                                                               text=True, encoding='utf-8').splitlines() if line]


report = {'source_rows_preserved': {}, 'original_revisions_read': 0, 'dataset_revisions_read': 0,
          'after_container_recreation': args.after_recreate}
for table in ('artifacts', 'artifact_revisions', 'schema_definitions', 'command_receipts', 'audit',
              'datasets', 'input_snapshots', 'process_runs', 'dataset_revisions', 'data_records'):
    sql = 'SELECT row_to_json(t) FROM "' + table + '" t'
    original = rows(source_client, sql)
    current = {json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows(target_client, sql)}
    if not all(json.dumps(row, sort_keys=True, ensure_ascii=False) in current for row in original):
        raise AssertionError('Original rows differ in ' + table)
    report['source_rows_preserved'][table] = len(original)
settings = dict(line.split('=', 1) for line in (root / '.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
bindings = json.loads(settings['BERVEL_ACCESS_KEYS'].strip("'"))
keys = {item['data_scope']: item['key'] for item in bindings}
opener = build_opener(ProxyHandler({}))


def get(path, scope):
    request = Request('http://127.0.0.1:8011/api/v0.1' + path,
                      headers={'Authorization': 'Bearer ' + keys[scope]})
    with opener.open(request, timeout=10) as response:
        return response.read()


for row in rows(source_client, 'SELECT row_to_json(t) FROM artifact_revisions t'):
    metadata = row['metadata']
    content = get(f"/artifacts/{row['artifact_id']}/revisions/{row['revision']}/content", metadata['data_scope'])
    assert hashlib.sha256(content).hexdigest() == metadata['sha256']
    report['original_revisions_read'] += 1
for row in rows(source_client, 'SELECT row_to_json(t) FROM dataset_revisions t'):
    # The envelope carries the data scope; artifacts remains its authoritative owner.
    scope = rows(source_client, "SELECT row_to_json(t) FROM (SELECT data_scope FROM artifacts WHERE artifact_id='" + row['artifact_id'] + "') t")[0]['data_scope']
    result = json.loads(get(f"/datasets/{row['dataset_id']}/revisions/{row['revision']}", scope))
    records = json.loads(get(f"/datasets/{row['dataset_id']}/revisions/{row['revision']}/records", scope))
    assert result['record_count'] == row['record_count'] == len(records['records'])
    report['dataset_revisions_read'] += 1
path = root / '.dev/local/docker' / ('migration-after-recreate.json' if args.after_recreate else 'migration-validation.json')
path.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
