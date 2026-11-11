"""Real HTTP origin regression: Docker DNS alias must survive document links."""
from contextlib import contextmanager
from http.client import HTTPConnection
import json
import socket
from threading import Thread

import pytest

from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient
from kontur_simulator import TEST_KEY, create_server


@contextmanager
def running_simulator():
    with create_server(0, profile='selection', today='2026-11-05T00:00:00+03:00',
                       max_requests_per_second=0) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


def get_card(server, key, host):
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    try:
        connection.request('GET', '/external/v1/purchases/' + key,
                           headers={'Host': host, 'X-Kontur-Apikey': TEST_KEY})
        response = connection.getresponse()
        assert response.status == 200
        return json.loads(response.read())
    finally:
        connection.close()


def test_real_client_fetches_documents_through_docker_alias_without_origin_escape(monkeypatch):
    original_resolver = socket.getaddrinfo

    def local_alias(host, port, *args, **kwargs):
        # Only DNS is mapped. Requests, Host headers and response/document reads
        # traverse actual HTTP sockets; no client guard or transport is mocked.
        return original_resolver('127.0.0.1' if host == 'kontur-simulator' else host, port, *args, **kwargs)

    monkeypatch.setattr(socket, 'getaddrinfo', local_alias)
    with running_simulator() as server:
        key = next(iter(server.purchases))
        generated_card = server.purchases[key].card()
        source = KonturClient(TEST_KEY, base_url=f'http://kontur-simulator:{server.server_port}', min_interval=0)
        tender = source.fetch_tender(key)
        expected = f'http://kontur-simulator:{server.server_port}/documents/{key}.txt'
        assert tender['source_card']['Docs'][0]['Url'] == expected
        assert tender['documents'][0]['url'] == expected
        assert 'Техническая спецификация' in tender['documents'][0]['text']
        assert server.purchases[key].card() == generated_card
        assert generated_card['Docs'][0]['Url'] == f'/documents/{key}.txt'


@pytest.mark.parametrize('authority', ['localhost:18765', 'kontur-simulator:18765',
                                        'kontur-simulator', '[::1]:18765'])
def test_card_honors_valid_request_authority_including_forwarded_port(authority):
    with running_simulator() as server:
        key = next(iter(server.purchases))
        card = get_card(server, key, authority)
        assert card['Docs'][0]['Url'] == f'http://{authority}/documents/{key}.txt'


@pytest.mark.parametrize('authority', ['external.example:18765', 'evil@localhost:18765',
                                        'localhost:18765/elsewhere', 'localhost:18765?q=1',
                                        'localhost:18765#fragment', 'localhost:99999'])
def test_card_does_not_advertise_untrusted_or_malformed_authorities(authority):
    with running_simulator() as server:
        key = next(iter(server.purchases))
        card = get_card(server, key, authority)
        assert card['Docs'][0]['Url'] == f'http://127.0.0.1:{server.server_port}/documents/{key}.txt'
