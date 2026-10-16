"""Read-only backup of the explicitly selected Open Server databases and immutable files."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pg-bin', type=Path, required=True)
parser.add_argument('--host', default='127.0.1.49')
args = parser.parse_args()
key = root / '.dev/local/core-stage-2/encryption.env'
if not key.is_file():
    raise ValueError('The source n8n encryption key is required for a restorable backup.')
backup = root / '.dev/local/docker/backups' / datetime.now(timezone.utc).strftime('source-%Y%m%dT%H%M%SZ')
backup.mkdir(parents=True)
client = [str(args.pg_bin / 'psql.exe'), '-h', args.host, '-U', 'postgres', '-w', '-At']


def query(database, sql):
    return subprocess.check_output([*client, '-d', database, '-c', sql], text=True).strip()


manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'source_host': args.host,
            'databases': {}, 'objects': {}}
for database in ('bervel_core_stage31', 'bervel_core_spike_n8n'):
    dump = backup / (database + '.dump')
    subprocess.run([str(args.pg_bin / 'pg_dump.exe'), '-h', args.host, '-U', 'postgres', '-w',
                    '-d', database, '--format=custom', '--no-owner', '--no-acl', '--file', str(dump)], check=True)
    info = {'dump': dump.name, 'sha256': hashlib.sha256(dump.read_bytes()).hexdigest(), 'tables': {}}
    tables = ('artifacts', 'artifact_revisions', 'datasets', 'dataset_revisions', 'data_records') if database.endswith('stage31') else ('workflow_entity', 'execution_entity')
    for table in tables:
        info['tables'][table] = int(query(database, 'SELECT count(*) FROM "' + table + '"'))
    manifest['databases'][database] = info
objects = root / '.dev/local/core-stage-3.1/objects'
with tarfile.open(backup / 'objects.tar', 'w') as archive:
    for path in sorted(objects.rglob('*')):
        if path.is_file():
            if path.is_symlink():
                raise ValueError('Refuse symlink in immutable object source')
            relative = path.relative_to(objects).as_posix()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if path.name != digest:
                raise ValueError('Source object hash does not match its key')
            manifest['objects'][relative] = digest
            archive.add(path, arcname=relative, recursive=False)
if key.exists():
    shutil.copy2(key, backup / 'n8n-encryption.env')
(backup / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps({'backup': str(backup), 'databases': {name: data['tables'] for name, data in manifest['databases'].items()},
                  'object_count': len(manifest['objects'])}, indent=2))
