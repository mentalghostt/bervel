"""Exercise the native worker and real durable inbox at a controlled CLI boundary."""
import io
import json
from urllib.error import HTTPError
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bervel.core.errors import CoreError
from bervel.modules.chat.api import chat_router
from bervel.modules.chat.service import ChatService
from bervel.modules.inspection.infrastructure.cli import NativeCliClient
from bervel.modules.inspection.native import NativeInspectionWorkflow
from test_inspection_native import ACTOR, turn
from test_inspection_steering_repository import inbox


class SteeringCli:
    poll_interval = 0

    def __init__(self, repo, sid, parent, *, lose_ack=False, restart=False, rejected=False):
        self.repo, self.sid, self.parent = repo, sid, parent
        self.lose_ack, self.restart, self.rejected = lose_ack, restart, rejected
        self.starts, self.reads, self.sent, self.receipts = 0, 0, [], []

    def job(self, state='running'):
        return {'state': state, 'thread_id': 'existing-native-thread', 'answer': 'Combined answer',
                'items': [], 'exports': [], 'followups': list(self.receipts)}

    def start(self, actor, current):
        self.starts += 1
        return self.job()

    def read(self, actor, current):
        self.reads += 1
        return self.job('completed' if self.receipts or self.reads > 3 else 'running')

    def steer(self, actor, current, message):
        row = self.repo.read(actor, self.sid)['turns'][1]
        assert row['delivery_state'] == 'sending', 'Sending must be durable before HTTP'
        assert current['turn_id'] == self.parent
        self.sent.append(message['question'])
        self.receipts.append({'message_id': str(message['turn_id']), 'state': 'rejected' if self.rejected else 'accepted'})
        if self.lose_ack:
            self.lose_ack = False
            raise CoreError('INSPECTION_CLI_TRANSPORT', 'lost acknowledgement')
        return self.job()


@pytest.mark.parametrize('lose_ack,restart', [(False, False), (True, False), (False, True)])
def test_worker_delivers_raw_followup_without_new_start_and_recovers_ack(inbox, lose_ack, restart):
    repo, sid, parent = inbox
    raw = '  exact\nPASSWORD=raw-private-input\n '
    child = repo.enqueue(ACTOR, sid, 'child', raw)
    if restart:
        with repo.claim():
            repo.mark_followup_sending(ACTOR, sid, parent, UUID(child['turn_id']))
    client = SteeringCli(repo, sid, parent, lose_ack=lose_ack, restart=restart)
    service = ChatService(repo, None, workflow=NativeInspectionWorkflow(client, None))
    assert service.run_once()
    values = service.read(ACTOR, sid)['turns']
    assert client.sent == [raw] and client.starts == (0 if restart else 1)
    assert values[0]['state'] == 'completed' and values[0]['answer'] == 'Combined answer'
    assert values[1]['state'] == 'completed' and values[1]['delivery_state'] == 'accepted' and values[1]['answer'] is None
    assert not service.run_once()


def test_native_client_posts_parent_identity_and_exact_message():
    current = turn()
    message = {'turn_id': UUID('00000000-0000-0000-0000-000000000001'), 'question': '  raw\n '}
    captured = []
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    class Endpoint:
        def open(self, request, **kwargs):
            captured.append(request)
            return Response(json.dumps({'request_id': str(current['turn_id']), 'session_id': str(current['session_id']),
                'actor_id': ACTOR.actor_id, 'data_scope': ACTOR.data_scope, 'state': 'running',
                'followups': [{'message_id': str(message['turn_id']), 'state': 'accepted'}]}).encode())
    client = NativeCliClient('http://cli', 'private', lambda _: 'key')
    client.opener = Endpoint()
    response = client.steer(ACTOR, current, message)
    assert response['followups'][0]['state'] == 'accepted'
    assert captured[0].full_url == f"http://cli/v1/inspection/jobs/{current['turn_id']}/messages"
    assert json.loads(captured[0].data) == {'message_id': str(message['turn_id']), 'question': '  raw\n ',
        'session_id': str(current['session_id']), 'actor_id': ACTOR.actor_id, 'data_scope': ACTOR.data_scope}


def test_inspection_http_exposes_followups_and_assistant_schema_stays_unchanged(inbox):
    repo, sid, parent = inbox
    app = FastAPI()
    app.include_router(chat_router(ChatService(repo, None), lambda: ACTOR, prefix='/api/v0.1/inspection'))
    http = TestClient(app)
    response = http.post(f'/api/v0.1/inspection/sessions/{sid}/messages', json={'command_key': 'child', 'content': 'Extra'})
    assert response.status_code == 202
    assert response.json()['followup_of'] == str(parent) and response.json()['delivery_state'] == 'pending'
    from bervel.modules.chat.api import TurnResponse
    assert 'followup_of' not in TurnResponse.model_json_schema()['properties']


@pytest.mark.parametrize('receipt', [{'message_id': 'child', 'state': 'invented'}, {'state': 'accepted'}, 'invalid'])
def test_native_client_rejects_malformed_followup_receipts(receipt):
    current = turn()
    class Endpoint:
        def open(self, *args, **kwargs):
            return io.BytesIO(json.dumps({'request_id': str(current['turn_id']), 'session_id': str(current['session_id']),
                'actor_id': ACTOR.actor_id, 'data_scope': ACTOR.data_scope, 'state': 'running', 'followups': [receipt]}).encode())
    client = NativeCliClient('http://cli', 'private', lambda _: 'key')
    client.opener = Endpoint()
    with pytest.raises(CoreError) as error:
        client.read(ACTOR, current)
    assert error.value.code == 'INSPECTION_CLI_TRANSPORT'


def test_multiple_messages_wait_for_receipts_and_reach_cli_in_fifo_order(inbox):
    repo, sid, parent = inbox
    ids = [repo.enqueue(ACTOR, sid, key, key)['turn_id'] for key in ('first', 'second', 'third')]
    class Client:
        poll_interval = 0
        def __init__(self): self.accepted, self.pending, self.sent, self.starts = [], None, [], 0
        def job(self):
            receipts = [{'message_id': mid, 'state': 'accepted'} for mid in self.accepted]
            if self.pending: receipts.append({'message_id': self.pending, 'state': 'queued'})
            return {'state': 'completed' if len(self.accepted) == 3 else 'running', 'items': [], 'exports': [],
                'answer': 'All three', 'thread_id': 'same-thread', 'followups': receipts}
        def start(self, *args): self.starts += 1; return self.job()
        def steer(self, actor, current, message):
            assert self.pending is None, 'Do not pass earlier unconfirmed input'
            self.pending = str(message['turn_id'])
            self.sent.append(message['question'])
            return self.job()
        def read(self, *args):
            assert self.pending is not None, 'Every poll here must follow durable delivery'
            self.accepted.append(self.pending); self.pending = None
            return self.job()
    client = Client()
    service = ChatService(repo, None, workflow=NativeInspectionWorkflow(client, None))
    assert service.run_once()
    assert client.starts == 1 and client.sent == ['first', 'second', 'third'] and client.accepted == ids
    assert all(row['delivery_state'] == 'accepted' for row in repo.read(ACTOR, sid)['turns'][1:])


@pytest.mark.parametrize('receipt', ['accepted', 'unknown', 'rejected'])
def test_restart_recovers_existing_receipt_without_reposting(inbox, receipt):
    repo, sid, parent = inbox
    child = UUID(repo.enqueue(ACTOR, sid, 'child', 'Recovery')['turn_id'])
    with repo.claim():
        repo.mark_followup_sending(ACTOR, sid, parent, child)
    class Client:
        poll_interval = 0
        def read(self, *args):
            return {'state': 'completed', 'items': [], 'exports': [], 'answer': 'Done',
                'followups': [{'message_id': str(child), 'state': receipt}]}
        def start(self, *args): raise AssertionError('Never restart parent')
        def steer(self, *args): raise AssertionError('Receipt is already durable')
    service = ChatService(repo, None, workflow=NativeInspectionWorkflow(Client(), None))
    assert service.run_once()
    value = service.read(ACTOR, sid)['turns'][1]
    assert value['state'] == {'accepted': 'completed', 'unknown': 'failed', 'rejected': 'queued'}[receipt]
    assert value['delivery_state'] == (None if receipt == 'rejected' else receipt)


def test_terminal_parent_recovers_missing_post_receipt_with_same_id(inbox):
    repo, sid, parent = inbox
    child = UUID(repo.enqueue(ACTOR, sid, 'child', 'Never reached runner')['turn_id'])
    with repo.claim():
        repo.mark_followup_sending(ACTOR, sid, parent, child)
    class Client:
        poll_interval = 0
        def __init__(self): self.ids = []
        def read(self, *args): return {'state': 'completed', 'items': [], 'exports': [], 'answer': 'Done', 'followups': []}
        def steer(self, actor, current, message):
            self.ids.append(message['turn_id'])
            return {**self.read(), 'followups': [{'message_id': str(message['turn_id']), 'state': 'rejected'}]}
    client = Client()
    service = ChatService(repo, None, workflow=NativeInspectionWorkflow(client, None))
    assert service.run_once()
    assert client.ids == [child]
    value = service.read(ACTOR, sid)['turns'][1]
    assert value['followup_of'] is None and value['state'] == 'queued'


@pytest.mark.parametrize('status', [400, 401, 403, 409, 413, 422])
def test_message_http_refusal_is_terminal_for_message_without_transport_retry(status):
    class Endpoint:
        def open(self, request, **kwargs):
            raise HTTPError(request.full_url, status, 'refused', {}, io.BytesIO(b'{"error":{"code":"IDEMPOTENCY_CONFLICT"}}'))
    client = NativeCliClient('http://cli', 'private', lambda _: 'key')
    client.opener = Endpoint()
    with pytest.raises(CoreError) as error:
        client.steer(ACTOR, turn(), {'turn_id': 'message', 'question': 'raw'})
    assert error.value.code == 'INSPECTION_CLI_MESSAGE_REJECTED'


def test_refused_message_fails_without_ending_or_restarting_parent(inbox):
    repo, sid, parent = inbox
    repo.enqueue(ACTOR, sid, 'child', 'Conflicting receipt')
    class Client(SteeringCli):
        def steer(self, actor, current, message):
            self.sent.append(message['question'])
            raise CoreError('INSPECTION_CLI_MESSAGE_REJECTED', 'Conflicting receipt')
    client = Client(repo, sid, parent)
    service = ChatService(repo, None, workflow=NativeInspectionWorkflow(client, None))
    assert service.run_once()
    values = service.read(ACTOR, sid)['turns']
    assert client.sent == ['Conflicting receipt'] and client.starts == 1
    assert values[0]['state'] == 'completed' and values[1]['state'] == 'failed' and values[1]['delivery_state'] == 'unknown'


@pytest.mark.parametrize('receipt,expected', [('accepted', 'completed'), ('rejected', 'blocked'), ('unknown', 'failed')])
def test_stop_recovers_inflight_receipt_and_blocks_untouched_messages(inbox, receipt, expected):
    repo, sid, parent = inbox
    first = repo.enqueue(ACTOR, sid, 'first', 'Already sending')['turn_id']
    repo.enqueue(ACTOR, sid, 'second', 'Still pending')
    class Client:
        poll_interval = 0
        def __init__(self): self.sent, self.stops = [], 0
        def start(self, *args): return {'state': 'running', 'items': [], 'followups': []}
        def steer(self, actor, current, message):
            self.sent.append(str(message['turn_id']))
            repo.cancel(ACTOR, sid, parent, 'stop')
            raise CoreError('INSPECTION_CLI_TRANSPORT', 'Post acknowledgement lost before Stop')
        def cancel(self, *args):
            self.stops += 1
            return {'state': 'cancelled', 'items': [], 'exports': [],
                'followups': [{'message_id': first, 'state': receipt}]}
        def read(self, *args): raise AssertionError('Stop must take precedence over ordinary polling')
    client = Client()
    service = ChatService(repo, None, workflow=NativeInspectionWorkflow(client, None))
    assert service.run_once()
    values = service.read(ACTOR, sid)['turns']
    assert client.sent == [first] and client.stops == 1
    assert values[0]['state'] == 'blocked' and values[1]['state'] == expected and values[2]['state'] == 'blocked'
    assert values[2]['delivery_state'] == 'pending'
    assert not service.run_once()
