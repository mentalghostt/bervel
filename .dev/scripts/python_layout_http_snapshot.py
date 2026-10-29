"""Hash existing HTTP reads; run through stdin in API before/after deployment."""
import hashlib
import json
import os
from urllib.request import Request, build_opener, ProxyHandler

from sqlalchemy import create_engine, text

bindings = json.loads(os.environ['BERVEL_ACCESS_KEYS'])
opener = build_opener(ProxyHandler({}))
engine = create_engine(os.environ['BERVEL_DATABASE_URL'], hide_parameters=True)
results = {}


def get(path, scope, actor=None):
    key = next(item['key'] for item in bindings if item['data_scope'] == scope and
               (actor is None or item['actor_id'] == actor) and 'artifact:read' in item['permissions'])
    with opener.open(Request('http://127.0.0.1:8011/api/v0.1' + path, headers={'Authorization': 'Bearer ' + key}), timeout=30) as response:
        content = response.read()
        results[path] = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                         'content_type': response.headers.get('Content-Type')}
    return content


with engine.connect() as connection:
    for item in connection.execute(text('SELECT metadata FROM artifact_revisions')).scalars():
        base = f"/artifacts/{item['artifact_id']}/revisions/{item['revision']}"
        content = get(base + '/content', item['data_scope'])
        assert hashlib.sha256(content).hexdigest() == item['sha256']
        get(base, item['data_scope'])
        get(base + '/preview', item['data_scope'])
    for row in connection.execute(text('SELECT d.dataset_id, d.revision, a.data_scope FROM dataset_revisions d JOIN artifacts a ON a.artifact_id=d.artifact_id')).mappings():
        base = f"/datasets/{row['dataset_id']}/revisions/{row['revision']}"
        get(base, row['data_scope'])
        get(base + '/records?limit=100', row['data_scope'])
    for row in connection.execute(text('SELECT * FROM chat_sessions')).mappings():
        get('/chat/sessions/' + str(row['session_id']), row['data_scope'], row['actor_id'])
    for row in connection.execute(text('SELECT * FROM radar_runs')).mappings():
        base = '/radar/runs/' + str(row['run_id'])
        get(base, row['data_scope'], row['actor_id'])
        if row['state'] == 'completed':
            assert get(base + '/report.pdf', row['data_scope'], row['actor_id']).startswith(b'%PDF')
engine.dispose()
print(json.dumps(results, sort_keys=True))
