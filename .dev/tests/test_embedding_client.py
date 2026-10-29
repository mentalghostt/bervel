"""Real HTTP boundary; server fixtures stand in only for the external provider."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from bervel.core.errors import CoreError


@contextmanager
def provider(status, body, headers=None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            supplied=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path == '/v1/embeddings'
            assert supplied['model'] == 'text-embedding-3-small'
            assert self.headers['Authorization'] == 'Bearer test-secret'
            self.send_response(status); self.send_header('Content-Type','application/json')
            for key,value in (headers or {}).items():self.send_header(key,value)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}/v1'
    finally: server.shutdown(); server.server_close(); thread.join()


def test_reorders_provider_vectors_by_input_index():
    from bervel.core.infrastructure.embeddings import OpenAIEmbeddings
    with provider(200,{'model':'text-embedding-3-small','data':[
        {'index':1,'embedding':[0.0,1.0]},{'index':0,'embedding':[1.0,0.0]}]}) as url:
        client=OpenAIEmbeddings('test-secret',base_url=url,dimensions=2,min_interval=0)
        assert client.embed(['Болт','Гайка']) == [[1.0,0.0],[0.0,1.0]]


@pytest.mark.parametrize('data',[
    [{'index':0,'embedding':[float('nan'),1]}],
    [{'index':0,'embedding':[0,0]}],
    [{'index':0,'embedding':[1]}],
    [{'index':0,'embedding':[1,0]},{'index':0,'embedding':[0,1]}],
])
def test_invalid_vectors_cannot_enter_the_index(data):
    from bervel.core.infrastructure.embeddings import OpenAIEmbeddings
    with provider(200,{'model':'text-embedding-3-small','data':data}) as url:
        with pytest.raises(CoreError) as error:
            OpenAIEmbeddings('test-secret',base_url=url,dimensions=2,min_interval=0).embed(['Болт'])
        assert error.value.code == 'EMBEDDING_INVALID_RESPONSE'


def test_rate_limit_is_explicit_and_not_retried():
    from bervel.core.infrastructure.embeddings import OpenAIEmbeddings
    with provider(429,{'error':{'message':'provider-private-secret'}}) as url:
        with pytest.raises(CoreError) as error:
            OpenAIEmbeddings('test-secret',base_url=url,dimensions=2,min_interval=0).embed(['Болт'])
        assert error.value.code == 'EMBEDDING_RATE_LIMIT'
        assert 'provider-private-secret' not in error.value.message


def test_regional_block_is_not_misreported_as_a_bad_key():
    from bervel.core.infrastructure.embeddings import OpenAIEmbeddings
    with provider(403,{'error':{'code':'unsupported_country_region_territory','message':'provider-private-secret'}}) as url:
        with pytest.raises(CoreError) as error:
            OpenAIEmbeddings('test-secret',base_url=url,dimensions=2,min_interval=0).embed(['Болт'])
        assert error.value.code=='EMBEDDING_REGION_UNAVAILABLE'
        assert 'регион' in error.value.message
        assert 'provider-private-secret' not in error.value.message


def test_missing_key_is_reported_before_sending_data():
    from bervel.core.infrastructure.embeddings import OpenAIEmbeddings
    with pytest.raises(CoreError) as error: OpenAIEmbeddings('').embed(['Болт'])
    assert error.value.code == 'EMBEDDING_UNCONFIGURED'


@pytest.mark.parametrize('budget',[8000,24000])
def test_provider_header_cannot_raise_a_worker_allocated_budget(budget):
    from bervel.core.infrastructure.embeddings import OpenAIEmbeddings
    with provider(200,{'model':'text-embedding-3-small','data':[{'index':0,'embedding':[1,0]}]},
                  {'x-ratelimit-limit-tokens':'40000'}) as url:
        client=OpenAIEmbeddings('test-secret',base_url=url,dimensions=2,min_interval=0,token_budget=budget)
        client.embed(['Болт'])
        assert client.token_budget==budget
