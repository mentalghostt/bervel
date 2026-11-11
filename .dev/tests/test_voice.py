import asyncio
import base64
import json
from dataclasses import replace
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext


def app_with(client, permissions=frozenset({'settings:read', 'settings:write', 'chat:write'})):
    from bervel.core.api.voice import voice_router
    app=FastAPI()
    @app.exception_handler(CoreError)
    async def error(request, exception):
        status={'FORBIDDEN':403,'VOICE_TOO_LARGE':413,'VOICE_INVALID_AUDIO':422,'VOICE_UNSUPPORTED_AUDIO':415}.get(exception.code,503)
        return JSONResponse({'error':{'code':exception.code,'message':exception.message}},status_code=status)
    app.include_router(voice_router(client,lambda: ActorContext('voice-user','voice-scope',permissions)))
    return app

class Stub:
    def __init__(self):self.calls=[]
    def settings_request(self,method,path,body=None):
        self.calls.append((method,path,body))
        return {'configured':bool(body),'state':'configured' if body else 'unconfigured','model':'whisper-large-v3-turbo'}
    async def transcribe(self,audio,mime_type,filename):
        self.calls.append((audio,mime_type,filename))
        return {'text':'Голосовой черновик'}

def test_voice_routes_secret_write_only_multipart_draft_and_rights():
    stub=Stub()
    with TestClient(app_with(stub)) as client:
        assert client.get('/api/v0.1/voice/settings').json()['configured'] is False
        response=client.put('/api/v0.1/voice/settings',json={'api_key':'private'})
        assert response.status_code==200 and 'private' not in response.text
        assert stub.calls[-1]==('PUT','/v1/voice/settings',{'api_key':'private'})
        response=client.post('/api/v0.1/voice/transcribe',files={'file':('voice.webm',b'\x1aE\xdf\xa3','audio/webm;codecs=opus')})
        assert response.json()=={'text':'Голосовой черновик'}
        assert stub.calls[-1]==(b'\x1aE\xdf\xa3','audio/webm','voice.webm')
    before=len(stub.calls)
    with TestClient(app_with(stub,frozenset({'chat:read'}))) as client:
        assert client.get('/api/v0.1/voice/settings').status_code==403
        assert client.put('/api/v0.1/voice/settings',json={'api_key':'private'}).status_code==403
        assert client.post('/api/v0.1/voice/transcribe',files={'file':('x.wav',b'x','audio/wav')}).status_code==403
    assert len(stub.calls)==before

def test_voice_rejects_empty_wrong_mime_size_names_without_runner_call():
    stub=Stub()
    with TestClient(app_with(stub)) as client:
        for name,data,mime,expected in [('voice.wav',b'', 'audio/wav',422),('voice.html',b'x','text/html',415),('voice.wav',b'x'*(5*1024*1024+1),'audio/wav',413),('../escape.wav',b'x','audio/wav',422)]:
            response=client.post('/api/v0.1/voice/transcribe',files={'file':(name,data,mime)})
            assert response.status_code==expected,response.text
        assert client.put('/api/v0.1/voice/settings',json={'api_key':'bad\nkey'}).status_code==422
    assert not stub.calls

def test_voice_adapter_transports_bytes_and_maps_safe_errors(monkeypatch):
    from bervel.core.infrastructure.voice import VoiceClient
    calls=[]
    def request(self,method,path,body=None,connection_holder=None):
        calls.append((method,path,body))
        return {'text':'draft','private':'not public'} if path=='/v1/transcribe' else {'configured':False,'state':'unconfigured','model':'whisper-large-v3-turbo','api_key':'must not leak'}
    monkeypatch.setattr(VoiceClient,'_request',request)
    voice=VoiceClient('http://runner:8090')
    assert voice.settings_request('GET','/v1/voice/settings')=={'configured':False,'state':'unconfigured','model':'whisper-large-v3-turbo'}
    assert asyncio.run(voice.transcribe(b'abc','audio/wav','voice.wav'))=={'text':'draft'}
    assert calls[-1]==('POST','/v1/transcribe',{'audio_base64':base64.b64encode(b'abc').decode(),'mime_type':'audio/wav','filename':'voice.wav'})

def test_voice_adapter_safe_failure_has_no_raw_provider_details(monkeypatch):
    from bervel.core.infrastructure.voice import VoiceClient
    import bervel.core.infrastructure.voice as module
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,*args,**kwargs):raise OSError('private-key raw diagnostic')
        def close(self):pass
    monkeypatch.setattr(module,'HTTPConnection',Connection)
    with pytest.raises(CoreError) as caught:VoiceClient('http://runner:8090').settings_request('GET','/v1/voice/settings')
    assert 'private-key' not in str(caught.value)

def test_voice_adapter_cancellation_closes_socket_without_sending_more_audio():
    from bervel.core.infrastructure.voice import VoiceClient
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    entered=threading.Event()
    disconnected=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            entered.set()
            self.connection.settimeout(2)
            if self.rfile.read(1)==b'':disconnected.set()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    worker.start()
    async def run():
        task=asyncio.create_task(VoiceClient(f'http://127.0.0.1:{server.server_port}').transcribe(b'abc','audio/wav','voice.wav'))
        assert await asyncio.to_thread(entered.wait,2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert await asyncio.to_thread(disconnected.wait,2)
    try:asyncio.run(run())
    finally:server.shutdown();server.server_close();worker.join(2)

@pytest.mark.parametrize('status,code',[(401,'VOICE_AUTH_ERROR'),(403,'VOICE_AUTH_ERROR'),(429,'VOICE_BUSY'),(504,'VOICE_TIMEOUT'),(502,'VOICE_FAILED'),(503,'VOICE_UNCONFIGURED')])
def test_voice_adapter_maps_runner_status_and_never_relays_error_text(monkeypatch,status,code):
    import bervel.core.infrastructure.voice as module
    class Response:
        def read(self,*args):return json.dumps({'error':{'code':code,'message':'private raw provider key'}}).encode()
    Response.status=status
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,*args,**kwargs):pass
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(module,'HTTPConnection',Connection)
    with pytest.raises(CoreError) as caught:module.VoiceClient('http://runner:8090').settings_request('GET','/v1/voice/settings')
    assert caught.value.code==code
    assert 'private raw' not in str(caught.value)
