"""Caller budgets cross the model port and the real runner HTTP adapter."""
import inspect
import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from bervel.core.errors import CoreError
from bervel.core.infrastructure.codex import CodexClient
from bervel.core.model_gateway import ModelClient


def require_deadline_parameter():
    for client in (ModelClient, CodexClient):
        parameter = inspect.signature(client.generate).parameters.get('timeout_seconds')
        assert parameter is not None, f'{client.__name__}.generate must accept the caller time budget'
        assert parameter.kind == inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None


def capture_http(monkeypatch, response=None):
    calls = []

    def external(request, *, timeout):
        calls.append({'method': request.method, 'url': request.full_url,
                      'payload': json.loads(request.data) if request.data is not None else None,
                      'timeout': timeout})
        return BytesIO(json.dumps(response or {'answer': 'fixture answer', 'model': 'fixture-model'}).encode())

    monkeypatch.setattr('bervel.core.infrastructure.codex.urlopen', external)
    return calls


@pytest.mark.parametrize('seconds,expected_ms', [(1, 1000), (2.3459, 2345), (0.2, 1000), (240, 180000)])
def test_model_adapter_sends_bounded_millisecond_budget_and_matching_transport_timeout(monkeypatch, seconds, expected_ms):
    require_deadline_parameter()
    calls = capture_http(monkeypatch)

    result = CodexClient('http://runner.fixture').generate('deadline', 'Question', timeout_seconds=seconds)

    assert result == {'answer': 'fixture answer', 'model': 'fixture-model'}
    assert len(calls) == 1
    call = calls[0]
    assert call['method'] == 'POST'
    assert call['url'] == 'http://runner.fixture/v1/generate'
    assert call['payload'] == {'request_id': 'deadline', 'prompt': 'Question', 'timeout_ms': expected_ms}
    assert type(call['payload']['timeout_ms']) is int
    assert expected_ms / 1000 < call['timeout'] <= min(190, expected_ms / 1000 + 2)


def test_omitted_deadline_keeps_legacy_generate_contract(monkeypatch):
    calls = capture_http(monkeypatch)

    CodexClient('http://runner.fixture/').generate('legacy', 'Question')

    assert calls == [{'method': 'POST', 'url': 'http://runner.fixture/v1/generate',
                      'payload': {'request_id': 'legacy', 'prompt': 'Question'}, 'timeout': 190}]


def test_deadline_preserves_images_schema_and_native_web_contract(monkeypatch):
    require_deadline_parameter()
    source = {'title': 'Fixture source', 'url': 'https://source.fixture/page'}
    calls = capture_http(monkeypatch, {'answer': '{"answer":"fixture"}', 'model': 'fixture-model', 'sources': [source]})
    images = [{'mime_type': 'image/png', 'base64': 'fixture'}]
    schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}, 'required': ['answer']}

    result = CodexClient('http://runner.fixture').generate('native', 'Question', images=images,
        output_schema=schema, web_search=True, timeout_seconds=3)

    assert result['sources'] == [source]
    assert calls[0]['payload'] == {'request_id': 'native', 'prompt': 'Question', 'images': images,
                                   'output_schema': schema, 'web_search': True, 'timeout_ms': 3000}


def test_settings_request_keeps_existing_timeout_and_payload(monkeypatch):
    calls = capture_http(monkeypatch, {'configured': True})

    result = CodexClient('http://runner.fixture').settings_request('PUT', '/v1/settings', {'model': 'fixture-model'})

    assert result == {'configured': True}
    assert calls == [{'method': 'PUT', 'url': 'http://runner.fixture/v1/settings',
                      'payload': {'model': 'fixture-model'}, 'timeout': 190}]


def test_runner_deadline_failure_is_safe_and_never_retried(monkeypatch):
    require_deadline_parameter()
    calls = []

    def external(request, *, timeout):
        calls.append((request, timeout))
        body = BytesIO(b'{"error":{"code":"MODEL_TIMEOUT","message":"PRIVATE diagnostic"}}')
        raise HTTPError(request.full_url, 504, 'PRIVATE failure', {}, body)

    monkeypatch.setattr('bervel.core.infrastructure.codex.urlopen', external)

    with pytest.raises(CoreError) as failure:
        CodexClient('http://runner.fixture').generate('unknown-outcome', 'Question', timeout_seconds=1)

    assert failure.value.code == 'MODEL_TIMEOUT'
    assert 'PRIVATE' not in str(failure.value)
    assert len(calls) == 1


@pytest.mark.parametrize('seconds', [True, '1', float('nan'), float('inf'), -float('inf')])
def test_invalid_model_budget_is_rejected_before_http(monkeypatch, seconds):
    require_deadline_parameter()
    calls = capture_http(monkeypatch)

    with pytest.raises(CoreError) as failure:
        CodexClient('http://runner.fixture').generate('invalid-budget', 'Question', timeout_seconds=seconds)

    assert failure.value.code == 'VALIDATION_ERROR'
    assert calls == []
