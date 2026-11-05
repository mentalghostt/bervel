"""Native chat integration with real scoped PostgreSQL and unchanged assistant DTOs."""
from dataclasses import replace
from uuid import UUID
import pytest
from sqlalchemy import select
from fastapi import FastAPI
from fastapi.testclient import TestClient
from bervel.core.infrastructure.documents import PostgresDocumentRepository
from bervel.core.errors import CoreError
from bervel.modules.chat.service import ChatService
from bervel.modules.chat.api import chat_router
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from bervel.modules.chat.infrastructure.tables import chat_turns
from bervel.modules.inspection.native import NativeInspectionWorkflow
from test_inspection_native import ACTOR, Jobs


@pytest.fixture
def native(pg_engine):
    repository=PostgresChatRepository(pg_engine,PostgresDocumentRepository(pg_engine),mode='inspection')
    jobs=Jobs([{'state':'running','thread_id':'same-native-thread','items':[{'id':'one','type':'command_execution','command':'python -V','status':'in_progress'}]},
        {'state':'completed','thread_id':'same-native-thread','answer':'Native answer','items':[{'id':'one','type':'command_execution','command':'python -V','status':'completed','aggregated_output':'Python 3.12','exit_code':0}],'exports':[]},
        {'state':'completed','thread_id':'same-native-thread','answer':'Followup','items':[],'exports':[]}])
    return ChatService(repository,None,None,workflow=NativeInspectionWorkflow(jobs,None)),repository,jobs


def test_native_raw_input_progress_and_thread_survive_postgres_roundtrip(native,pg_engine):
    service,repository,jobs=native
    sid=UUID(service.create(ACTOR,'create')['session_id'])
    raw='  exact\nPASSWORD=raw-private-input\n'+ 'Q'*8000 +'\n '
    service.send(ACTOR,sid,'one',raw)
    assert service.run_once()
    first=service.read(ACTOR,sid)
    assert first['engine']=='codex-cli' and first['cli_thread_id']=='same-native-thread'
    assert first['turns'][0]['question']==raw and first['turns'][0]['state']=='completed'
    assert first['turns'][0]['actions'][0]['operation']=='command.run'
    assert first['turns'][0]['progress_text']
    service.send(ACTOR,sid,'two','follow up')
    assert service.run_once()
    second=repository.read(ACTOR,sid)
    assert second['cli_thread_id']=='same-native-thread' and len(second['turns'])==2
    assert all(call[1]['session_id']==sid for call in jobs.started)
    with pg_engine.connect() as connection:
        assert connection.execute(select(chat_turns.c.question).where(chat_turns.c.session_id==sid).order_by(chat_turns.c.ordinal)).scalars().first()==raw
    with pytest.raises(CoreError):service.read(replace(ACTOR,actor_id='foreign'),sid)


def test_http_native_input_has_no_4000_limit_but_assistant_keeps_it(native,pg_engine):
    service,_,_=native
    ordinary=ChatService(PostgresChatRepository(pg_engine,PostgresDocumentRepository(pg_engine)),None,None)
    app=FastAPI();app.include_router(chat_router(service,lambda:ACTOR,prefix='/api/v0.1/inspection'))
    app.include_router(chat_router(ordinary,lambda:ACTOR))
    http=TestClient(app)
    for prefix,limit in [('/api/v0.1/inspection',False),('/api/v0.1/chat',True)]:
        response=http.post(prefix+'/sessions',json={'command_key':'create'})
        session=response.json();sid=session['session_id']
        assert ('engine' in session) is (not limit)
        sent=http.post(prefix+'/sessions/'+sid+'/messages',json={'command_key':'one','content':'x'*6000})
        assert sent.status_code==(422 if limit else 202)
    schema=http.get('/openapi.json').json()
    assert 'maxLength' not in schema['components']['schemas']['InspectionMessageBody']['properties']['content']
    assert schema['components']['schemas']['MessageBody']['properties']['content']['maxLength']==4000


def test_inspection_read_confirms_only_its_own_exact_send_receipt(native):
    service, _, jobs = native
    app = FastAPI()
    app.include_router(chat_router(service, lambda: ACTOR, prefix='/api/v0.1/inspection'))
    http = TestClient(app)
    prefix = '/api/v0.1/inspection/sessions'
    first = service.create(ACTOR, 'first')['session_id']
    other = service.create(ACTOR, 'other')['session_id']
    old = service.send(ACTOR, UUID(first), 'message:old', 'тот же вопрос')
    accepted = service.send(ACTOR, UUID(first), 'message:accepted', 'тот же вопрос')
    for session_id, key, expected in (
        (first, 'message:accepted', accepted['turn_id']),
        (first, 'message:old', old['turn_id']),
        (other, 'message:accepted', None),
        (first, 'message:absent', None),
        (first, 'first', None),
    ):
        response = http.get(f'{prefix}/{session_id}', params={'pending_command_key': key})
        assert response.status_code == 200
        assert response.json()['accepted_turn_id'] == expected
    assert len(service.read(ACTOR, UUID(first))['turns']) == 2
    with pytest.raises(CoreError):
        service.read(replace(ACTOR, actor_id='foreign'), UUID(first), pending_command_key='message:accepted')
    assert not jobs.started
