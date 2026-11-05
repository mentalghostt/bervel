"""Review N1/N2: active CLI transport recovery and mask-before-display boundaries."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import io,json
from threading import Event, Thread
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from uuid import UUID,uuid4
import pytest
from sqlalchemy import select
from bervel.core.errors import CoreError
from bervel.modules.inspection.native import NativeInspectionWorkflow
from bervel.modules.inspection.infrastructure.cli import NativeCliClient
from bervel.modules.inspection.redaction import SecretRedactor
from bervel.modules.chat.service import ChatService
from bervel.modules.chat.infrastructure.tables import chat_turns
from test_inspection_native import ACTOR,Jobs,turn
from test_inspection_native_repository import native


def test_transport_timeout_and_generic_404_are_not_authoritative_job_outcome():
    client=NativeCliClient('http://codex:8090','private',lambda actor:'app-key')
    current=turn()
    class Endpoint:
        def __init__(self,error):self.error=error
        def open(self,*args,**kwargs):raise self.error
    for error in [URLError('offline'), HTTPError('http://codex',503,'unavailable',{},io.BytesIO(b'{}')),
                  HTTPError('http://codex',404,'proxy route missing',{},io.BytesIO(b'{"error":{"code":"NOT_FOUND"}}'))]:
        client.opener=Endpoint(error)
        with pytest.raises(CoreError) as outcome:client.read(ACTOR,current)
        assert outcome.value.code=='INSPECTION_CLI_TRANSPORT'


def test_lost_start_ack_recovers_same_job_without_second_post_and_keeps_exports(monkeypatch):
    artifact=uuid4();sleeps=[]
    monkeypatch.setattr('bervel.modules.inspection.native.sleep',sleeps.append)
    class Client:
        poll_interval=0.1
        starts=0;reads=0
        def start(self,actor,current):
            self.starts+=1
            raise CoreError('INSPECTION_CLI_TRANSPORT','lost ACK')
        def read(self,actor,current):
            self.reads+=1
            if self.reads==1:raise CoreError('INSPECTION_CLI_TRANSPORT','still offline')
            return {'state':'completed','thread_id':'same-thread','answer':'done','items':[],'exports':[{'artifact_id':str(artifact)}]}
    client=Client();states=[]
    def metadata(actor,ref):return {'artifact_id':str(ref.artifact_id),'filename':'saved.txt','media_type':'text/plain','source_role':'test_fixture','source':{}}
    answer,_,cards=NativeInspectionWorkflow(client,SimpleNamespace(metadata=metadata)).run(ACTOR,turn(),checkpoint=states.append,cancelled=lambda:False)
    assert client.starts==1 and client.reads==2 and answer=='done'
    assert cards[0]['artifact_id']==str(artifact)
    assert states[0]['phase']=='research' and states[0]['native']['state']=='running'
    assert sleeps[1]>sleeps[0] and max(sleeps)<=5


def test_authoritative_missing_job_does_not_restart():
    class Client:
        poll_interval=0
        def read(self,*args):raise CoreError('INSPECTION_CLI_NOT_FOUND','Definitive missing job')
        def start(self,*args):raise AssertionError('must never restart')
    current=turn();current['interrupted']=True
    answer,_,cards=NativeInspectionWorkflow(Client(),None).run(ACTOR,current,checkpoint=lambda _:None,cancelled=lambda:False)
    assert current['workflow']['phase']=='blocked' and 'неизвестен' in answer and cards==[]


def test_active_poll_outage_preserves_running_stop_and_retries_cancel(native,pg_engine,monkeypatch):
    service,repository,_=native
    sid=UUID(service.create(ACTOR,'n1-create')['session_id'])
    tid=UUID(service.send(ACTOR,sid,'n1-send','raw native question')['turn_id'])
    outage_recovered=Event();inspect_running=Event();release=Event();errors=[]
    class Client:
        poll_interval=0
        starts=0;reads=0;cancels=0
        def start(self,actor,current):self.starts+=1;return {'state':'running','thread_id':'same-thread','items':[]}
        def read(self,actor,current):
            self.reads+=1
            if self.reads==1:raise CoreError('INSPECTION_CLI_TRANSPORT','connection dropped')
            if self.reads==2:
                inspect_running.set()
                assert release.wait(5)
            return {'state':'running','thread_id':'same-thread','items':[]}
        def cancel(self,actor,current):
            self.cancels+=1
            if self.cancels==1:raise CoreError('INSPECTION_CLI_TRANSPORT','cancel ACK lost')
            return {'state':'cancelled','thread_id':'same-thread','answer':'','items':[],'exports':[]}
    client=Client();service.workflow=NativeInspectionWorkflow(client,None)
    def worker():
        try:service.run_once()
        except BaseException as error:errors.append(error)
    thread=Thread(target=worker);thread.start()
    try:
        assert inspect_running.wait(2), 'worker must recover polling instead of finishing blocked'
        view=service.read(ACTOR,sid)['turns'][0]
        assert view['state']=='running' and not view['cancel_requested']
        assert 'связ' in view['progress_text'].casefold()
        service.cancel(ACTOR,sid,tid,'stop')
        with pg_engine.connect() as connection:
            row=connection.execute(select(chat_turns.c.state,chat_turns.c.cancel_requested).where(chat_turns.c.turn_id==tid)).one()
            assert row.state=='running' and row.cancel_requested
    finally:
        release.set();thread.join(5)
    assert not thread.is_alive() and not errors
    assert client.starts==1 and client.cancels==2
    final=service.read(ACTOR,sid)['turns'][0]
    assert final['state']=='blocked' and 'остановлен' in final['answer'].casefold()


@pytest.mark.parametrize('kind,offset',[('progress',790),('agent_message',790),('command_execution',590)])
def test_full_native_progress_is_masked_before_any_display_shortening(kind,offset):
    secret='sensitive-production-credential-77553311';raw='x '* (offset//2)+secret+' end'
    current=turn(raw);current.update(actor_id=ACTOR.actor_id,data_scope=ACTOR.data_scope,actor_permissions=list(ACTOR.permissions),snapshot=[])
    job={'state':'completed','thread_id':'thread','answer':'done','items':[],'exports':[]}
    if kind=='progress':job['progress']=raw
    else:job['items']=[{'id':'one','type':kind,'status':'completed',('text' if kind=='agent_message' else 'command'):raw}]
    class Repository:
        @contextmanager
        def claim(self):yield current
        def checkpoint(self,tid,state):self.saved=deepcopy(state)
        def cancel_requested(self,*args):return False
        def finish(self,tid,**values):self.finished=values
    repo=Repository();jobs=Jobs([job]);redactor=SecretRedactor([secret])
    service=ChatService(repo,None,workflow=NativeInspectionWorkflow(jobs,None),content_filter=redactor.clean)
    assert service.run_once()
    public=json.dumps(repo.finished,ensure_ascii=False)
    assert secret[:10] not in public and '[СКРЫТО]' in public
    assert jobs.started[0][1]['question']==raw


def test_native_session_title_masks_full_question_before_120_char_preview(native):
    service,repository,jobs=native
    secret='sensitive-production-credential-119988'
    service.content_filter=SecretRedactor([secret]).clean
    raw='x '*57+secret+' end'
    sid=UUID(service.create(ACTOR,'title-create')['session_id'])
    service.send(ACTOR,sid,'title-send',raw)
    assert repository.read(ACTOR,sid)['turns'][0]['question']==raw
    title=service.list_sessions(ACTOR)['sessions'][0]['title']
    assert secret[:6] not in title and '[СКРЫ' in title and len(title)<=120


def test_incomplete_http_body_is_recoverable_transport_failure():
    from http.client import IncompleteRead
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):raise IncompleteRead(b'{"state":',20)
    class Endpoint:
        def open(self,*args,**kwargs):return Response()
    client=NativeCliClient('http://codex:8090','private',lambda actor:'app-key');client.opener=Endpoint()
    with pytest.raises(CoreError) as failure:client.read(ACTOR,turn())
    assert failure.value.code=='INSPECTION_CLI_TRANSPORT'


def test_only_specific_missing_job_error_is_authoritative():
    class Endpoint:
        def open(self,*args,**kwargs):
            raise HTTPError('http://codex',404,'missing',{},io.BytesIO(b'{"error":{"code":"NATIVE_JOB_NOT_FOUND"}}'))
    client=NativeCliClient('http://codex:8090','private',lambda actor:'app-key');client.opener=Endpoint()
    with pytest.raises(CoreError) as failure:client.read(ACTOR,turn())
    assert failure.value.code=='INSPECTION_CLI_NOT_FOUND'
