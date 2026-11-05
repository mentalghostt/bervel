"""Small real API/worker/model smoke test, run through stdin in the API container."""
from datetime import datetime, timezone
import hashlib
import json
import os
import time
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

bindings = json.loads(os.environ['BERVEL_ACCESS_KEYS'])
binding = next(b for b in bindings if {'chat:read', 'chat:write', 'process:start', 'process:read', 'artifact:read', 'dataset:read'} <= set(b['permissions']))
opener = build_opener(ProxyHandler({}))
base = 'http://127.0.0.1:8011/api/v0.1'
tag = 'layout-' + uuid4().hex


def call(path, body=None, *, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(base + path, data=data, headers={'Authorization': 'Bearer ' + binding['key'], 'Content-Type': 'application/json'})
    with opener.open(request, timeout=30) as response:
        content = response.read()
        return content if raw else json.loads(content)


def wait(path, select=lambda value: value):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        value = select(call(path))
        if value['state'] in {'completed', 'failed'}:
            assert value['state'] == 'completed', value.get('error')
            return value
        time.sleep(1)
    raise AssertionError('Background job did not complete within four minutes')


session = call('/chat/sessions', {'command_key': tag + '-chat'})
sid = session['session_id']
assert call('/chat/sessions', {'command_key': tag + '-chat'})['session_id'] == sid
turn = call(f'/chat/sessions/{sid}/messages', {'command_key': tag + '-message',
    'content': 'Посмотри bervel-product-profile.csv: назови две группы продукции, которые там перечислены, со ссылкой на файл. Подтверждены ли этим файлом складские остатки?'})
completed = wait(f'/chat/sessions/{sid}', lambda value: next(t for t in value['turns'] if t['turn_id'] == turn['turn_id']))
assert completed['answer'] and any(s['filename'] == 'bervel-product-profile.csv' for s in completed['sources'])

print(json.dumps({'checked_at': datetime.now(timezone.utc).isoformat(), 'chat_session_id': sid,
    'chat_turn_id': turn['turn_id'], 'chat_completed_with_profile_source': True}, indent=2))
