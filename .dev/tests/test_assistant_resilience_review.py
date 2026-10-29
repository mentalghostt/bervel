"""Independent review regressions; mocks stop at external model HTTP responses."""
from dataclasses import replace
from io import BytesIO
import json
from urllib.error import URLError
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.core.infrastructure.codex import CodexClient
from test_assistant_resilience import HEADERS, answer, document, http_chat, plan, request_payload
from test_assistant_workflows import actor_with_chat, build_actions
from test_knowledge_resilience import UPSTREAM_DETAIL, ready, real_memory, register


@pytest.mark.parametrize('disconnect_at', ['before_effect', 'after_effect'])
def test_actual_model_transport_disconnect_has_a_truthful_answer_and_keeps_any_saved_effect(
        services, pg_engine, monkeypatch, disconnect_at):
    """The actual adapter maps connection loss to TRANSIENT_DEPENDENCY_ERROR."""
    def external_http(request, **options):
        envelope = json.loads(request.data)
        value = request_payload(envelope['prompt'])
        if disconnect_at == 'before_effect':
            raise URLError('fixture provider disconnected')
        if 'AUTHORIZATION_REQUEST' in value:
            response = {'answer': '{"decision":"allow"}', 'model': 'fixture'}
        elif not value['ACTION_RESULTS']:
            response = plan('file.create', document(), 'Создай PDF')
        else:
            raise URLError('fixture provider disconnected')
        return BytesIO(json.dumps(response).encode())

    monkeypatch.setattr('bervel.core.infrastructure.codex.urlopen', external_http)
    model = CodexClient('http://external-fixture.invalid')
    with http_chat(services, pg_engine, model) as (http, service, actions, actor, send, read):
        send('Создай PDF с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        assert turn['state'] == ('blocked' if disconnect_at == 'before_effect' else 'completed'), turn['error']
        assert turn['answer'] and turn['error'] is None
        assert 'fixture provider' not in json.dumps(turn)
        if disconnect_at == 'after_effect':
            assert len(turn['results']) == 1 and turn['results'][0]['kind'] == 'file'
            assert len(turn['actions']) == 1 and turn['actions'][0]['state'] == 'completed'
            downloaded = http.get(turn['results'][0]['download_url'], headers=HEADERS)
            assert downloaded.status_code == 200 and downloaded.content.startswith(b'%PDF-')
            assert len(services[0].list(actor)['artifacts']) == 1
        else:
            assert turn['actions'] == [] and turn['results'] == []
            assert services[0].list(actor)['artifacts'] == []
        assert not service.run_once()


@pytest.mark.parametrize('failure_kind', ['WEB_SEARCH_TIMEOUT', 'WEB_SEARCH_FAILED', 'WEB_SEARCH_BUSY', 'invalid_sources'])
def test_failed_web_search_cannot_be_repeated_by_rephrasing_the_query_under_a_new_key(
        services, pg_engine, failure_kind):
    class Model:
        def generate(self, request_id, prompt, **options):
            if options.get('web_search'):
                # A paid provider request may have happened despite the missing response.
                if failure_kind == 'invalid_sources':
                    return {'answer': 'UNVERIFIED MANUFACTURING CLAIM', 'model': 'native-search',
                        'sources': [{'title': 'Invalid source', 'url': 'javascript:alert(1)'}]}
                raise CoreError(failure_kind, 'fixture provider response not received')
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            results = value['ACTION_RESULTS']
            if not results:
                return plan('web.search', {'query': 'Zeta manufacturing official'}, 'Найди в интернете')
            if len(results) == 1:
                return plan('web.search', {'query': 'Zeta manufacturing official production'}, 'Найди в интернете')
            return answer('Веб-поиск не ответил; сведения не подтверждены.')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        send('Найди в интернете производство Zeta')
        assert service.run_once()
        turn = read()
        assert len(turn['actions']) == 1, turn['actions']
        expected_code = 'WEB_SEARCH_FAILED' if failure_kind == 'invalid_sources' else failure_kind
        assert turn['actions'][0]['error']['code'] == expected_code
        assert turn['results'] == [] and turn['sources'] == []
        assert services[0].list(actor)['artifacts'] == []
        assert not service.run_once()


def test_failed_create_cannot_be_reported_as_success_by_a_later_model_answer(services, pg_engine):
    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if not value['ACTION_RESULTS']:
                return plan('file.create', document(filename='../analysis.pdf'), 'Создай PDF')
            return answer('Готово: файл успешно создан, скачайте analysis.pdf.')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        send('Создай PDF с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked', turn['error']
        assert len(turn['actions']) == 1 and turn['actions'][0]['state'] == 'failed'
        assert turn['results'] == [] and services[0].list(actor)['artifacts'] == []
        assert 'файл успешно создан' not in turn['answer'].casefold(), turn['answer']
        assert any(phrase in turn['answer'].casefold() for phrase in ('не заверш', 'не создан', 'проверку', 'ошиб'))


@pytest.mark.parametrize('mock_source', [False, True])
def test_actual_file_read_preserves_lexical_search_limitation_and_mock_provenance_notice(
        services, pg_engine, mock_source):
    memory, knowledge, repository, provider, artifacts, _ = real_memory(services, pg_engine)
    actor = actor_with_chat(services)
    text = 'product,quantity,data_kind\nБолт М16,11,mock\n' if mock_source else 'Болт М16 ГОСТ 7798.'
    reference = register(artifacts, actor, text, 'mock-fasteners.csv' if mock_source else 'fasteners.txt',
        'text/csv' if mock_source else 'text/plain')
    ready(knowledge, repository, actor, reference)
    provider.error = CoreError('EMBEDDING_REGION_UNAVAILABLE', UPSTREAM_DETAIL)
    actions = build_actions(services, pg_engine)
    turn_id = uuid4()

    result = actions.execute(actor, turn_id, 0, 'file.read', {'artifact_id': str(reference.artifact_id)},
        memory=memory, question='Болт М16')

    source = result['data']['sources'][0]
    assert source['artifact_id'] == str(reference.artifact_id)
    assert source['retrieval_mode'] == 'lexical'
    assert isinstance(source.get('notice'), str) and 'поиск по словам' in source['notice'].casefold(), source
    assert source['is_mock'] is mock_source
    if mock_source:
        assert 'демонстрацион' in source['notice'].casefold()
    assert UPSTREAM_DETAIL not in json.dumps(result, ensure_ascii=False)
    durable = actions.receipts.read(actor, turn_id)
    assert durable[0]['state'] == 'completed' and durable[0]['result'] == result


def test_bounded_known_filename_failures_end_with_truthful_answer_and_no_hidden_retry(services, pg_engine):
    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            index = len(value['ACTION_RESULTS'])
            return plan('file.create', document(filename=f'../analysis-{index}.pdf'), 'Создай PDF')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        send('Создай PDF с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked', turn['error']
        assert turn['answer'] and turn['error'] is None
        assert len(turn['actions']) == 2
        assert all(row['state'] == 'failed' and row['error']['code'] == 'VALIDATION_ERROR'
            for row in turn['actions'])
        assert turn['results'] == [] and services[0].list(actor)['artifacts'] == []
        assert any(phrase in turn['answer'].casefold() for phrase in ('не заверш', 'не создан', 'не удалось создать', 'лимит', 'шаг'))
        assert not service.run_once()
        assert len(read()['actions']) == 2


def test_actual_settings_adapter_lost_write_response_is_unknown_and_cannot_reapply(services, pg_engine, monkeypatch):
    actions = build_actions(services, pg_engine)
    actor = actor_with_chat(services)
    actor = replace(actor, permissions=actor.permissions | {'settings:write'})
    actions.settings = CodexClient('http://external-fixture.invalid')
    turn_id = uuid4()
    arguments = {'provider': 'api_key', 'model': 'test-model'}
    applied_settings = []

    def external_http(request, **options):
        assert request.method == 'PUT' and request.full_url.endswith('/v1/settings')
        applied_settings.append(json.loads(request.data))
        raise URLError('fixture response was lost after settings were applied')

    monkeypatch.setattr('bervel.core.infrastructure.codex.urlopen', external_http)
    with pytest.raises(CoreError) as first:
        actions.execute(actor, turn_id, 0, 'settings.update', arguments)
    assert first.value.code == 'ACTION_OUTCOME_UNKNOWN'
    durable = actions.receipts.read(actor, turn_id)
    assert len(durable) == 1 and durable[0]['state'] == 'unknown'
    assert durable[0]['error']['code'] == 'ACTION_OUTCOME_UNKNOWN' and durable[0]['result'] is None
    with pytest.raises(CoreError) as replay:
        actions.execute(actor, turn_id, 0, 'settings.update', arguments)
    assert replay.value.code == 'ACTION_OUTCOME_UNKNOWN'
    assert applied_settings == [arguments]
