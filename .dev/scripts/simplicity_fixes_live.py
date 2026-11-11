"""Controlled synthetic smoke: real HTTP/worker/model, no user contents in output."""
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
import sys
import time
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

required = {'artifact:read', 'artifact:write', 'dataset:read', 'dataset:publish', 'process:start', 'process:read', 'chat:read', 'chat:write'}
binding = next(b for b in json.loads(os.environ['BERVEL_ACCESS_KEYS']) if required <= set(b['permissions']))
opener = build_opener(ProxyHandler({}))
base = 'http://127.0.0.1:8011/api/v0.1'
tag = 'simplicity-' + uuid4().hex
checks = {}
created = {'artifacts': [], 'datasets': []}


def call(path, body=None, *, raw=False):
    payload = json.dumps(body).encode() if body is not None else None
    request = Request(base + path, data=payload, headers={'Authorization': 'Bearer ' + binding['key'], 'Content-Type': 'application/json'})
    with opener.open(request, timeout=30) as response:
        content = response.read()
        return content if raw else json.loads(content)


def upload(name, content):
    boundary = 'boundary-' + uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="command_key"\r\n\r\n{tag}-{name}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{tag}-{name}"\r\nContent-Type: text/csv\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
    request = Request(base + '/artifacts', data=body, headers={'Authorization': 'Bearer ' + binding['key'], 'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with opener.open(request, timeout=30) as response:
        ref = json.load(response)
    created['artifacts'].append(ref)
    original = call(f"/artifacts/{ref['artifact_id']}/revisions/1/content", raw=True)
    assert hashlib.sha256(original).digest() == hashlib.sha256(content).digest()
    return ref


def wait(path, select=lambda value: value):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        value = select(call(path))
        if value['state'].lower() in {'completed', 'failed'}:
            assert value['state'].lower() == 'completed', 'Synthetic background smoke failed; inspect safe stored status'
            return value
        time.sleep(1)
    raise AssertionError('Synthetic background job timed out')


if sys.argv[1:] == ['--quoted-cell']:
    # Final-candidate check after the review fix; one model call is sufficient.
    cell = ('a' * 250 + ',b,c\n') * 35
    content = ('id;description;value\n1;"' + cell + '";482\n').encode()
    ref = upload('quoted.csv', content)
    viewed = call(f"/artifacts/{ref['artifact_id']}/revisions/1/preview")
    assert viewed['columns'] == ['id', 'description', 'value'] and viewed['rows'] == [['1', cell, '482']]
    session = call('/chat/sessions', {'command_key': tag + '-quoted-chat'})
    path = '/chat/sessions/' + session['session_id']
    turn = call(path + '/messages', {'command_key': tag + '-quoted-message',
        'content': 'В последнем CSV назови значение колонки value в единственной строке. Сохрани точное число и ссылку на файл.'})
    result = wait(path, lambda v: next(t for t in v['turns'] if t['turn_id'] == turn['turn_id']))
    assert '482' in result['answer'] and {s['artifact_id'] for s in result['sources']} == {ref['artifact_id']}
    created.update(chat_session_id=session['session_id'], chat_turn_id=turn['turn_id'])
    print(json.dumps({'final_candidate_quoted_preview_and_real_chat': True, 'created': created}, indent=2))
    raise SystemExit(0)


# Preserve the separate educational contract, both direct and background paths.
educational = upload('educational.csv', b'line,name,quantity,unit\n1,Control Bolt,2,pcs\n')
command = {'artifact_id': educational['artifact_id'], 'artifact_revision': 1, 'command_key': tag + '-sync'}
dataset = call('/demo/document-review/extractions', command)
assert call('/demo/document-review/extractions', command) == dataset
created['datasets'].append(dataset)
assert call(f"/datasets/{dataset['dataset_id']}/revisions/1/records")['records'][0]['payload']['quantity']['value'] == '2'
job = call('/demo/document-review/processes', {**command, 'command_key': tag + '-background'})
created['process_run_id'] = job['process_run_id']
completed = wait('/processes/' + job['process_run_id'])
created['datasets'].append(completed['result'])
checks['educational_sync_and_background'] = True

# New generic path: CP1251 + pipe + quoted multiline, exact preview and chat source.
name = 'profile.csv'
profile = upload(name, 'Группа|Описание\nКрепёж|"Болты М16 ГОСТ 7798\nКонтрольное число 1729"\n'.encode('cp1251'))
preview = call(f"/artifacts/{profile['artifact_id']}/revisions/1/preview")
assert preview['columns'] == ['Группа', 'Описание'] and '1729' in preview['rows'][0][1]
checks['cp1251_pipe_multiline_preview'] = True
session_command = {'command_key': tag + '-chat'}
session = call('/chat/sessions', session_command)
assert call('/chat/sessions', session_command)['session_id'] == session['session_id']
created['chat_session_id'] = session['session_id']
message = {'command_key': tag + '-message', 'content': 'В последнем CSV назови контрольное число и группу продукции. Укажи файл и редакцию. Это тестовое извлечение, не подтверждение склада.'}
path = '/chat/sessions/' + session['session_id']
turn = call(path + '/messages', message)
assert call(path + '/messages', message)['turn_id'] == turn['turn_id']
created['chat_turn_id'] = turn['turn_id']
answer = wait(path, lambda value: next(t for t in value['turns'] if t['turn_id'] == turn['turn_id']))
assert '1729' in answer['answer'] and {s['artifact_id'] for s in answer['sources']} == {profile['artifact_id']}
assert call(path)['turns'][-1]['answer'] == answer['answer']
checks['real_chat_latest_csv_history_and_idempotency'] = True

print(json.dumps({'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': checks, 'created': created}, indent=2))
