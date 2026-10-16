"""Restore a selected backup only into empty, project-owned Docker databases."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

from docker_backup_keys import validate_backup_key

root = Path(__file__).resolve().parents[2]
docker = shutil.which('docker') or r'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('backup', type=Path)
args = parser.parse_args()
backup = args.backup.resolve()
if not backup.is_relative_to(root / '.dev/local/docker/backups'):
    raise ValueError('Backup must be inside the project backup directory')
manifest = json.loads((backup / 'manifest.json').read_text())
validate_backup_key(backup / 'n8n-encryption.env', root / '.env')


def compose(*arguments):
    return subprocess.check_output([docker, 'compose', *arguments], cwd=root, text=True).strip()


def sql(database, statement):
    return compose('exec', '-T', 'postgres', 'psql', '-U', 'postgres', '-d', database, '-At', '-c', statement)


targets = (
    ('bervel_core_stage31', 'bervel_core_stage31', 'bervel_core_stage31', 'artifacts'),
    ('bervel_core_spike_n8n', 'bervel_n8n', 'bervel_n8n', 'workflow_entity'),
)
# Validate all known prerequisites before copying or restoring either database.
for source, target, role, sentinel in targets:
    archive = backup / manifest['databases'][source]['dump']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['databases'][source]['sha256']:
        raise ValueError('Database archive checksum differs: ' + source)
    if sql(target, "SELECT to_regclass('public." + sentinel + "') IS NULL") != 't':
        raise RuntimeError('Refuse restore into populated database: ' + target)
with tarfile.open(backup / 'objects.tar') as archive:
    files = {member.name: member for member in archive.getmembers() if member.isfile()}
    if set(files) != set(manifest['objects']):
        raise ValueError('Object archive does not match its manifest')
    for relative, digest in manifest['objects'].items():
        if hashlib.sha256(archive.extractfile(files[relative]).read()).hexdigest() != digest:
            raise ValueError('Object archive checksum differs')
volume = subprocess.run([docker, 'volume', 'inspect', 'bervel_objects'], capture_output=True)
if volume.returncode == 0:
    subprocess.run([docker, 'run', '--rm', '--mount', 'type=volume,source=bervel_objects,target=/data,readonly',
                    'python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e',
                    'python', '-c', "from pathlib import Path; assert not any(Path('/data').iterdir()), 'Refuse restore into nonempty object store'"], check=True)
for source, target, role, sentinel in targets:
    archive = backup / manifest['databases'][source]['dump']
    compose('cp', str(archive), 'postgres:/tmp/bervel-source.dump')
    compose('exec', '-T', 'postgres', 'pg_restore', '-U', 'postgres', '--role=' + role,
            '--dbname=' + target, '--no-owner', '--no-acl', '--single-transaction', '--exit-on-error', '/tmp/bervel-source.dump')
    compose('exec', '-T', 'postgres', 'rm', '/tmp/bervel-source.dump')
    for table, expected in manifest['databases'][source]['tables'].items():
        actual = int(sql(target, 'SELECT count(*) FROM "' + table + '"'))
        if actual != expected:
            raise RuntimeError('Restore count differs: ' + target + '.' + table)

subprocess.run([docker, 'volume', 'create', '--label', 'com.docker.compose.project=bervel',
                '--label', 'com.docker.compose.volume=objects', 'bervel_objects'], check=True, capture_output=True)
extract = """import hashlib,json,os,pathlib,tarfile
root=pathlib.Path('/data')
if any(root.iterdir()): raise RuntimeError('Refuse restore into nonempty object store')
with tarfile.open('/backup/objects.tar') as archive: archive.extractall(root,filter='data')
manifest=json.loads(pathlib.Path('/backup/manifest.json').read_text())
for relative,digest in manifest['objects'].items():
 path=root/relative
 assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
for path in [root,*root.rglob('*')]: os.chown(path,10001,10001)
print('Restored and verified original objects:',len(manifest['objects']))
"""
subprocess.run([docker, 'run', '--rm', '--mount', 'type=volume,source=bervel_objects,target=/data',
                '--mount', 'type=bind,source=' + str(backup) + ',target=/backup,readonly',
                'python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e',
                'python', '-c', extract], check=True)
report = {'source_backup': str(backup), 'database_counts_match': True,
          'original_hashes_match': True, 'object_count': len(manifest['objects'])}
(root / '.dev/local/docker/source-restore.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))
