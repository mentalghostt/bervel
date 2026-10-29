"""Read-only fingerprint of pre-existing application data, no secret output."""
import hashlib
import json

from sqlalchemy import text
from bervel.core.config import Settings
from bervel.core.infrastructure.database import make_engine

engine = make_engine(Settings.from_environment().database_url)
tables = ('artifacts', 'artifact_revisions', 'datasets', 'dataset_revisions', 'data_records',
          'process_runs', 'process_jobs', 'process_attempts', 'input_snapshots', 'audit', 'command_receipts')
result = {}
with engine.connect() as connection:
    for name in tables:
        rows = sorted(connection.execute(text('SELECT row_to_json(t)::text FROM ' + name + ' t')).scalars())
        result[name] = {'count': len(rows), 'sha256': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
print(json.dumps(result, sort_keys=True))
engine.dispose()
