"""Native inspection transports raw input and CLI events without an app planner."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4, UUID
import pytest
from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.modules.chat.workflow import initial_workflow

ACTOR = ActorContext('native-user', 'native-scope', frozenset({'chat:read','chat:write','artifact:read','dataset:read'}))

class Jobs:
    poll_interval = 0
    def __init__(self, states): self.states, self.started, self.cancelled, self.reads = iter(states), [], [], []
    def start(self, actor, turn): self.started.append((actor, deepcopy(turn))); return next(self.states)
    def read(self, actor, turn): self.reads.append(str(turn['turn_id'])); return next(self.states)
    def cancel(self, actor, turn): self.cancelled.append(str(turn['turn_id'])); return next(self.states)


def turn(question='test'):
    return {'turn_id':uuid4(),'session_id':uuid4(),'question':question,'interrupted':False,'workflow':initial_workflow(unlimited_research=True)}


def test_native_question_is_exact_and_natural_answer_not_capped():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    question = '  raw\nPASSWORD=actual-request-value\n' + 'я' * 5000 + '\n '
    state = turn(question)
    jobs = Jobs([{'state':'running','thread_id':'native-thread','items':[]},
        {'state':'completed','thread_id':'native-thread','answer':'A'*40000,'model':'native-model','items':[],'exports':[]}])
    checkpoints=[]
    answer,model,results = NativeInspectionWorkflow(jobs,None).run(ACTOR,state,checkpoint=checkpoints.append,cancelled=lambda:False)
    assert jobs.started[0][1]['question'] == question
    assert answer == 'A'*40000 and model == 'native-model' and results == []
    assert state['workflow']['native']['thread_id'] == 'native-thread'
    assert state['workflow']['phase'] == 'completed'


def test_interrupted_worker_only_reads_existing_job_never_reposts():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    state=turn();state['interrupted']=True
    jobs=Jobs([{'state':'unknown','thread_id':'preserved','items':[],'error_code':'INTERRUPTED'}])
    answer,_,_=NativeInspectionWorkflow(jobs,None).run(ACTOR,state,checkpoint=lambda value:None,cancelled=lambda:False)
    assert state['workflow']['phase']=='blocked' and 'неизвестен' in answer
    assert jobs.started == [] and jobs.reads == [str(state['turn_id'])]
    assert state['workflow']['native']['thread_id'] == 'preserved'


def test_native_cancel_reaches_existing_job_and_returns_stopped():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    jobs=Jobs([{'state':'running','thread_id':'native-thread','items':[]}, {'state':'cancelled','thread_id':'native-thread','items':[],'exports':[]}])
    checks=iter([False,True])
    state=turn()
    answer,_,_=NativeInspectionWorkflow(jobs,None).run(ACTOR,state,checkpoint=lambda value:None,cancelled=lambda:next(checks))
    assert jobs.cancelled == [str(state['turn_id'])]
    assert 'остановлен' in answer.casefold() and state['workflow']['phase']=='blocked'


def test_native_events_project_readable_actions_without_json_dump():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    jobs=Jobs([{'state':'completed','thread_id':'native-thread','answer':'done','items':[
        {'id':'cmd-1','type':'command_execution','command':'python -V','status':'completed','aggregated_output':'Python 3.12','exit_code':0},
        {'id':'file-1','type':'file_change','status':'completed','changes':[{'path':'src/example.py','kind':'update'}]}], 'exports':[]}])
    state=turn();NativeInspectionWorkflow(jobs,None).run(ACTOR,state,checkpoint=lambda value:None,cancelled=lambda:False)
    actions=state['workflow']['native']['actions']
    assert [a['operation'] for a in actions] == ['command.run','code.change']
    assert actions[0]['result']['output'] == 'Python 3.12'
    assert actions[1]['result']['files'] == ['src/example.py']


def test_export_is_verified_by_artifact_metadata_not_model_card():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    artifact_id=uuid4();seen=[]
    def metadata(actor,ref):
        seen.append(ref.artifact_id)
        return {'artifact_id':str(ref.artifact_id),'filename':'actual.txt','media_type':'text/plain','source_role':'test_fixture','source':{}}
    jobs=Jobs([{'state':'completed','answer':'done','items':[],'exports':[{'artifact_id':str(artifact_id),'filename':'invented.pdf'}]}])
    _,_,cards=NativeInspectionWorkflow(jobs,SimpleNamespace(metadata=metadata)).run(ACTOR,turn(),checkpoint=lambda value:None,cancelled=lambda:False)
    assert seen == [artifact_id] and cards[0]['filename']=='actual.txt'


def test_native_service_skips_history_memory_model_and_input_filter():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    from bervel.modules.chat.service import ChatService
    state=turn('raw '+ 'Q'*5000); state.update(actor_id=ACTOR.actor_id,data_scope=ACTOR.data_scope,actor_permissions=list(ACTOR.permissions),snapshot=[])
    class Repository:
        def enqueue(self,actor,sid,key,content):
            assert content == state['question'];return {'question':content}
        def read(self,*args): raise AssertionError('native CLI owns history')
        @contextmanager
        def claim(self): yield state
        def checkpoint(self,tid,value): pass
        def cancel_requested(self,*args): return False
        def finish(self,tid,**values): self.finished=values
    def output_only(value):
        assert value != state['question'], 'raw input must never pass output filter'
        return value
    repo=Repository();jobs=Jobs([{'state':'completed','thread_id':'native-thread','answer':'done','items':[],'exports':[]}])
    service=ChatService(repo,None,None,workflow=NativeInspectionWorkflow(jobs,None),content_filter=output_only)
    service.send(ACTOR,state['session_id'],'send',state['question'])
    assert service.run_once() and repo.finished['answer']=='done'


def test_native_transport_sends_exact_question_and_rejects_foreign_job():
    import io,json
    from bervel.modules.inspection.infrastructure.cli import NativeCliClient
    current=turn('  \nexact PASSWORD=not-to-rewrite '+ 'я'*5000 +'  ')
    records=[]
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self,*args): self.close()
    class Endpoint:
        foreign=False
        def open(self,request,timeout):
            records.append((request.method,request.full_url,json.loads(request.data) if request.data else None,timeout))
            body={'request_id':str(current['turn_id']),'session_id':str(current['session_id']),
                'actor_id':'foreign' if self.foreign else ACTOR.actor_id,'data_scope':ACTOR.data_scope,'state':'running','thread_id':'native-thread','items':[],'exports':[]}
            return Response(json.dumps(body).encode())
    endpoint=Endpoint(); client=NativeCliClient('http://codex:8090','private-transport',lambda actor:'actual-app-key');client.opener=endpoint
    client.start(ACTOR,current)
    assert records[0][2]['question']==current['question']
    assert set(records[0][2])=={'request_id','session_id','actor_id','data_scope','question','api_key'}
    assert records[0][2]['api_key']=='actual-app-key' and records[0][3]==30
    client.read(ACTOR,current);client.cancel(ACTOR,current)
    assert [r[0] for r in records]==['POST','GET','POST']
    endpoint.foreign=True
    with pytest.raises(CoreError):client.read(ACTOR,current)


def test_unknown_export_is_not_a_created_attachment():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    def missing(*args):raise CoreError('NOT_FOUND','No artifact')
    jobs=Jobs([{'state':'completed','answer':'done','items':[],'exports':[{'artifact_id':str(uuid4()),'filename':'fake.pdf'}]}])
    current=turn()
    answer,_,cards=NativeInspectionWorkflow(jobs,SimpleNamespace(metadata=missing)).run(ACTOR,current,checkpoint=lambda _:None,cancelled=lambda:False)
    assert cards==[] and 'не подтверждена' in answer and current['workflow']['phase']=='partial'


def test_native_public_history_is_batched_without_losing_fields():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    from bervel.modules.chat.service import ChatService
    import json
    history={'turns':[{'question':'q','answer':'answer '*50000} for _ in range(16)]}
    calls=[]
    def redact(value):
        assert len(json.dumps(value).encode())<1024*1024
        calls.append(value);return value
    service=ChatService(None,None,workflow=NativeInspectionWorkflow(None,None),content_filter=redact)
    assert service._public(history)==history and len(calls)==16


def test_failed_native_job_preserves_only_confirmed_export_cards():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    artifact_id=uuid4()
    def metadata(actor,ref):return {'artifact_id':str(ref.artifact_id),'filename':'saved.txt','media_type':'text/plain','source_role':'test_fixture','source':{}}
    jobs=Jobs([{'state':'failed','thread_id':'same','error_code':'CLI_PROCESS_EXIT','items':[],'exports':[{'artifact_id':str(artifact_id)}]}])
    current=turn()
    answer,_,cards=NativeInspectionWorkflow(jobs,SimpleNamespace(metadata=metadata)).run(ACTOR,current,checkpoint=lambda _:None,cancelled=lambda:False)
    assert current['workflow']['phase']=='blocked' and 'CLI_PROCESS_EXIT' in answer
    assert cards[0]['artifact_id']==str(artifact_id)


def test_public_field_batching_preserves_secret_field_names():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    from bervel.modules.inspection.redaction import SecretRedactor
    from bervel.modules.chat.service import ChatService
    source={'turns':[{'answer':'safe line\n'*(120*1024),'password':'fresh-secret-not-yet-known'}]}
    service=ChatService(None,None,workflow=NativeInspectionWorkflow(None,None),content_filter=SecretRedactor().clean)
    assert 'fresh-secret-not-yet-known' not in str(service._public(source))


def test_output_filter_failure_finishes_blocked_without_replaying_cli():
    from bervel.modules.inspection.native import NativeInspectionWorkflow
    from bervel.modules.chat.service import ChatService
    current=turn();current.update(actor_id=ACTOR.actor_id,data_scope=ACTOR.data_scope,actor_permissions=list(ACTOR.permissions),snapshot=[])
    class Repository:
        @contextmanager
        def claim(self):yield current
        def checkpoint(self,tid,state):self.saved=state
        def cancel_requested(self,*args):return False
        def finish(self,tid,**values):self.finished=values
    def unavailable_for_answer(value):
        if value=='native-answer':raise CoreError('TRANSIENT_DEPENDENCY_ERROR','Output service unavailable')
        return value
    repo=Repository();jobs=Jobs([{'state':'completed','answer':'native-answer','items':[],'exports':[]}])
    service=ChatService(repo,None,workflow=NativeInspectionWorkflow(jobs,None),content_filter=unavailable_for_answer)
    assert service.run_once()
    assert repo.finished['outcome']=='blocked' and len(jobs.started)==1
