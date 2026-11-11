"""Read-only SQL/object/API snapshot, run through stdin inside the API container."""
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text

engine = create_engine(os.environ['BERVEL_DATABASE_URL'], hide_parameters=True)
result = {'tables': {}, 'objects': {}}
with engine.connect() as connection:
    connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    names = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")).scalars()
    for name in names:
        rows = connection.execute(text('SELECT row_to_json(t)::text FROM "' + name + '" t')).scalars()
        hashes = sorted(hashlib.sha256(row.encode()).hexdigest() for row in rows)
        result['tables'][name] = {'count': len(hashes), 'rows': hashes}
    for item in connection.execute(text('SELECT metadata FROM artifact_revisions')).scalars():
        digest = item['sha256']
        content = (Path(os.environ['BERVEL_OBJECT_ROOT']) / digest[:2] / digest).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest and len(content) == item['size']
        result['objects'][digest] = len(content)
with urlopen('http://127.0.0.1:8011/openapi.json') as response:
    result['openapi'] = json.load(response)
engine.dispose()
print(json.dumps(result, sort_keys=True))
