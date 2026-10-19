import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from bervel.core.errors import CoreError


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        responses = {}
        calls = []
        def do_GET(self):
            self.respond()
        def do_POST(self):
            self.respond()
        def respond(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.calls.append((self.command, self.path, dict(self.headers), json.loads(body) if body else None))
            status, value = self.responses.get((self.command, self.path), (404, {'message': 'private upstream details'}))
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{http.server_port}', Handler
    http.shutdown()
    thread.join()
    http.server_close()


def adapter(url):
    from bervel.infrastructure.n8n import N8nEngine
    return N8nEngine(url, 'api-key-private', 'webhook-private', 'workflow-id', 'bervel-csv', timeout=2)


def remote_execution(identifier='100', parent=None):
    return {'id': identifier, 'workflowId': 'workflow-id', 'workflowVersionId': 'version-v1',
        'status': 'running', 'retryOf': parent, 'workflowData': {'versionId': 'version-v1'},
        'data': {'resultData': {'runData': {'Start': [{'data': {'main': [[{'json': {'body': {
            'process_run_id': 'run-id', 'invocation_token': 'secret-token'}}}]]}}]}}}}


def test_published_version_headers_webhook_and_native_original_retry(server):
    url, handler = server
    handler.responses[('GET', '/api/v1/workflows/workflow-id')] = (200, {'id': 'workflow-id', 'active': True,
        'versionId': 'draft-v2', 'activeVersionId': 'version-v1'})
    handler.responses[('POST', '/webhook/bervel-csv')] = (200, {'message': 'Workflow was started'})
    handler.responses[('GET', '/api/v1/executions/100?includeData=true')] = (200, remote_execution())
    handler.responses[('POST', '/api/v1/executions/100/retry')] = (200, remote_execution('101', '100'))
    engine = adapter(url)
    assert engine.describe().version_id == 'version-v1'
    engine.start('run-id', 'secret-token')
    assert engine.execution('100').process_run_id == 'run-id'
    assert engine.retry('100').retry_of == '100'
    assert {name.lower(): value for name, value in handler.calls[0][2].items()}['x-n8n-api-key'] == 'api-key-private'
    webhook = next(call for call in handler.calls if call[1].startswith('/webhook'))
    assert webhook[2]['Authorization'] == 'Bearer webhook-private'
    assert 'api-key-private' not in str(webhook)
    assert handler.calls[-1][3] == {'loadWorkflow': False}


def test_safe_upstream_errors_and_draft_not_treated_as_published(server):
    url, handler = server
    with pytest.raises(CoreError) as caught:
        adapter(url).execution('missing')
    assert 'private upstream' not in caught.value.message
    handler.responses[('GET', '/api/v1/workflows/workflow-id')] = (200, {'id': 'workflow-id', 'active': False, 'versionId': 'draft'})
    with pytest.raises(CoreError, match='WORKFLOW_MISMATCH'):
        adapter(url).describe()


def test_execution_search_checks_running_and_bounded_pagination(server):
    url, handler = server
    query = '/api/v1/executions?workflowId=workflow-id&includeData=true&limit=100'
    handler.responses[('GET', query)] = (200, {'data': [], 'nextCursor': None})
    handler.responses[('GET', query + '&status=running')] = (200, {'data': [remote_execution()], 'nextCursor': None})
    assert [row.execution_id for row in adapter(url).find('workflow-id', 'run-id')] == ['100']
    handler.responses[('GET', query)] = (200, {'data': [], 'nextCursor': 'repeated-cursor'})
    handler.responses[('GET', query + '&cursor=repeated-cursor')] = (200, {'data': [], 'nextCursor': 'repeated-cursor'})
    with pytest.raises(CoreError, match='ENGINE_RESPONSE_UNKNOWN'):
        adapter(url).find('workflow-id', 'run-id')


def test_retry_missing_version_uses_identical_original_snapshot_only(server):
    url, handler = server
    parent, child = remote_execution(), remote_execution('101', '100')
    graph = {'nodes': [{'id': 'start', 'type': 'webhook'}], 'connections': {}, 'settings': {'executionOrder': 'v1'}}
    parent['workflowData'] = dict(graph)
    child['workflowData'] = dict(graph)
    child['workflowVersionId'] = None
    handler.responses[('GET', '/api/v1/executions/100?includeData=true')] = (200, parent)
    handler.responses[('GET', '/api/v1/executions/101?includeData=true')] = (200, child)
    assert adapter(url).execution('101').version_id == 'version-v1'
    child['workflowData'] = {**graph, 'nodes': [{'id': 'changed', 'type': 'webhook'}]}
    with pytest.raises(CoreError, match='WORKFLOW_MISMATCH'):
        adapter(url).execution('101')
    assert all('/workflows/' not in call[1] for call in handler.calls)
