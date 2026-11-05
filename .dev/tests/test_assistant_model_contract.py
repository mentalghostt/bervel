"""The Python runner boundary must preserve opt-in capabilities and verifiable citations."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from threading import Thread

import pytest

from bervel.core.errors import CoreError
from bervel.core.infrastructure.codex import CodexClient


def runner(payload, callback):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200); self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        return callback(CodexClient(f'http://127.0.0.1:{server.server_port}'), calls)
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_python_runner_forwards_typed_schema_and_explicit_web_search():
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer'], 'additionalProperties': False}
    def check(client, calls):
        result = client.generate('typed', 'query', output_schema=schema, web_search=True)
        assert result['sources'] == [{'title': 'Official', 'url': 'https://example.org/source'}]
        assert calls == [{'request_id': 'typed', 'prompt': 'query', 'output_schema': schema, 'web_search': True}]
    runner({'answer': 'answer', 'model': 'm', 'sources': [{'title': 'Official', 'url': 'https://example.org/source'}]}, check)


@pytest.mark.parametrize('sources', [None, [], [{'title': 'missing URL'}]])
def test_python_runner_does_not_claim_search_without_citation_receipt(sources):
    def check(client, _):
        with pytest.raises(CoreError) as error:
            client.generate('search', 'query', web_search=True)
        assert error.value.code == 'WEB_SEARCH_FAILED'
    runner({'answer': 'answer', 'model': 'm', 'sources': sources}, check)


def test_native_strict_schema_requires_every_object_property_and_optional_nulls_normalize():
    from bervel.modules.chat.planner import PLAN_SCHEMA, parse_plan
    def strict(schema):
        if isinstance(schema, dict):
            if schema.get('type') == 'object':
                assert set(schema['required']) == set(schema['properties']), 'Native strict provider rejects optional object properties'
            for value in schema.values(): strict(value)
        elif isinstance(schema, list):
            for value in schema: strict(value)
    strict(PLAN_SCHEMA)
    result = parse_plan(json.dumps({'answer': '', 'actions': [{'operation': 'file.list',
        'arguments': {'cursor': None, 'limit': None}, 'authorization_quote': 'Покажи файлы'}]}), 'Покажи файлы')
    assert result['actions'][0]['arguments'] == {}


