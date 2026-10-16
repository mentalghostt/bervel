"""Read restored SQL and original bytes through the installed API in an isolated database."""
import hashlib
import json
import os
from pathlib import Path
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from bervel.api import create_app
from bervel.config import Settings
from bervel.core.models import ActorContext

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / '.dev/tests'))
from http_client import ASGITestClient

database = os.environ['BERVEL_RESTORE_DATABASE']
if not database.startswith('bervel_restore_check_'):
    raise ValueError('Only an isolated restore-check database is allowed')
url = make_url(os.environ['BERVEL_TEST_DATABASE_URL']).set(database=database)
bindings = json.loads(os.environ['BERVEL_ACCESS_KEYS'])
keys = {item['data_scope']: item['key'] for item in bindings}
settings = Settings(url.render_as_string(hide_password=False), Path('/restore-objects'), {
    item['key']: ActorContext(item['actor_id'], item['data_scope'], frozenset(item['permissions'])) for item in bindings})
engine = create_engine(url)
counts = {}
originals = datasets = 0
with engine.connect() as connection, ASGITestClient(create_app(settings)) as client:
    for table in ('artifacts', 'artifact_revisions', 'datasets', 'dataset_revisions', 'data_records'):
        counts[table] = connection.execute(text('SELECT count(*) FROM ' + table)).scalar_one()
    for row in connection.execute(text('SELECT artifact_id, revision, metadata FROM artifact_revisions')).mappings():
        metadata = row['metadata']
        response = client.get(f"/api/v0.1/artifacts/{row['artifact_id']}/revisions/{row['revision']}/content",
            headers={'Authorization': 'Bearer ' + keys[metadata['data_scope']]})
        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == metadata['sha256']
        originals += 1
    for row in connection.execute(text('SELECT d.dataset_id,d.revision,d.record_count,a.data_scope FROM dataset_revisions d JOIN artifacts a ON d.artifact_id=a.artifact_id')).mappings():
        response = client.get(f"/api/v0.1/datasets/{row['dataset_id']}/revisions/{row['revision']}/records",
            headers={'Authorization': 'Bearer ' + keys[row['data_scope']]})
        assert response.status_code == 200
        assert len(response.json()['records']) == row['record_count']
        datasets += 1
engine.dispose()
report = {'database_counts': counts, 'original_revisions_read': originals, 'dataset_revisions_read': datasets}
expected = json.loads(Path('/backup/manifest.json').read_text(encoding='utf-8-sig'))['database_counts']
assert counts == expected
Path('/workspace/.dev/local/core-stage-3.1/backup-restore-check.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
