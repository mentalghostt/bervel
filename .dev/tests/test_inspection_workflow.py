import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.modules.inspection.actions import InspectionActions
from bervel.modules.inspection.workflow import InspectionWorkflow
from bervel.modules.inspection.redaction import SecretRedactor
from bervel.modules.chat.workflow import initial_workflow


class Reader:
    def __init__(self): self.reads = []; self.redactor = SecretRedactor(['known-raw-secret'])
    def read(self, operation, arguments):
        self.reads.append((operation, arguments))
        return {'path': arguments.get('path'), 'text': 'actual code', 'line_start': 1, 'line_end': 1}
    def redact(self, value): return self.redactor.clean(value)


class Receipts:
    def __init__(self): self.items = []
    def begin(self, actor, turn_id, ordinal, operation, arguments):
        existing = next((item for item in self.items if item['ordinal'] == ordinal), None)
        if existing: return existing
        value = {'action_id': str(uuid4()), 'ordinal': ordinal, 'operation': operation, 'arguments': arguments, 'state': 'new'}
        self.items.append(value); return value
    def finish(self, actor, action_id, *, result=None, error=None, unknown=False):
        item = next(item for item in self.items if item['action_id'] == str(action_id))
        item.update(state='unknown' if unknown else 'failed' if error else 'completed', result=result, error=error)


class Tickets:
    def __init__(self): self.created = []
    def create(self, actor, content, key):
        self.created.append((content, key))
        return {**content, 'ticket_id': str(uuid4()), 'status': 'created'}


def setup_actions():
    reader, receipts, tickets = Reader(), Receipts(), Tickets()
    return InspectionActions(reader=reader, receipts=receipts, tickets=tickets), reader, receipts, tickets


ACTOR = ActorContext('reviewer', 'area', frozenset({'chat:write', 'chat:read', 'artifact:read', 'dataset:read'}))


@pytest.mark.parametrize('operation', ['file.create', 'file.delete', 'file.prepare', 'settings.update', 'ticket.edit', 'container.exec', 'radar.analyse'])
def test_actions_reject_forbidden_tools_before_receipts_or_service_calls(operation):
    actions, reader, receipts, tickets = setup_actions()
    with pytest.raises(CoreError) as error:
        actions.execute(ACTOR, uuid4(), 0, operation, {})
    assert error.value.code == 'FORBIDDEN'
    assert receipts.items == [] and reader.reads == [] and tickets.created == []


def test_ticket_requires_server_authorization_and_replay_returns_the_same_result():
    actions, reader, receipts, tickets = setup_actions()
    content = {'title': 'Правка', 'problem': 'Ошибка', 'expected': '', 'actual': '', 'steps': [], 'context': '', 'links': []}
    turn = uuid4()
    with pytest.raises(CoreError): actions.execute(ACTOR, turn, 0, 'ticket.create', content)
    first = actions.execute(ACTOR, turn, 0, 'ticket.create', content, ticket_authorized=True)
    assert actions.execute(ACTOR, turn, 0, 'ticket.create', content, ticket_authorized=True) == first
    assert len(tickets.created) == 1


def test_workflow_reads_real_result_and_masks_model_output_before_return():
    actions, reader, receipts, tickets = setup_actions()
    class Model:
        def __init__(self): self.prompts = []
        def generate(self, request_id, prompt, **options):
            self.prompts.append(prompt)
            plan = {'answer': '', 'actions': [{'operation': 'system.read', 'arguments': {'path': 'src/bervel/app_setup.py'}, 'authorization_quote': ''}]} if len(self.prompts) == 1 else {'answer': 'actual code: known-raw-secret', 'actions': []}
            return {'answer': json.dumps(plan), 'model': 'test-double'}
    model = Model()
    turn = {'turn_id': uuid4(), 'question': 'Исследуй код', 'workflow': initial_workflow()}
    answer, _, results = InspectionWorkflow(ACTOR, turn, [], [], [], [], actions=actions, memory=None, model=model, checkpoint=lambda state: None).run()
    assert reader.reads == [('system.read', {'path': 'src/bervel/app_setup.py'})]
    assert 'actual code' in model.prompts[1]
    assert 'known-raw-secret' not in answer
    assert turn['workflow']['phase'] == 'completed'
    assert results[0]['data']['text'] == 'actual code'


def test_malicious_model_cannot_leave_inspection_even_with_exact_user_quote():
    actions, reader, receipts, tickets = setup_actions()
    model = SimpleNamespace(generate=lambda *args, **kwargs: {'answer': json.dumps({'answer': '', 'actions': [
        {'operation': 'file.create', 'arguments': {}, 'authorization_quote': 'создай файл'}]}), 'model': 'adversarial'})
    turn = {'turn_id': uuid4(), 'question': 'создай файл', 'workflow': initial_workflow()}
    with pytest.raises(CoreError):
        InspectionWorkflow(ACTOR, turn, [], [], [], [], actions=actions, memory=None, model=model, checkpoint=lambda state: None).run()
    assert receipts.items == [] and tickets.created == []


def test_real_ticket_validator_accepts_a_masked_diagnostic_but_still_rejects_secrets():
    from bervel.modules.tickets.service import validate_content
    content = {'title': 'Правка', 'problem': 'API_KEY=[СКРЫТО]', 'expected': '', 'actual': '', 'steps': [], 'context': 'Authorization: Bearer [СКРЫТО]', 'links': []}
    assert validate_content(content)['problem'] == content['problem']
    with pytest.raises(CoreError): validate_content({**content, 'problem': 'API_KEY=[СКРЫТО]real-secret'})
    for suffix in ['.real-secret', ',real-secret', '].real-secret']:
        with pytest.raises(CoreError): validate_content({**content, 'problem': 'API_KEY=[СКРЫТО]' + suffix})


def test_real_queue_finishes_and_refilters_history_when_a_secret_becomes_known(pg_engine):
    from bervel.core.infrastructure.documents import PostgresDocumentRepository
    from bervel.modules.chat import ChatService
    from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
    from bervel.modules.chat.infrastructure.actions import PostgresActionReceipts
    from uuid import UUID
    reader = Reader()
    repository = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine), mode='inspection')
    actions = InspectionActions(reader=reader, receipts=PostgresActionReceipts(pg_engine), tickets=Tickets())
    model = SimpleNamespace(generate=lambda *args, **kwargs: {'answer': json.dumps({'answer': 'Ответ для старого секрета', 'actions': []}), 'model': 'test-double'})
    service = ChatService(repository, None, model, actions=actions, workflow=InspectionWorkflow, content_filter=reader.redact)
    sid = UUID(service.create(ACTOR, 'new')['session_id'])
    service.send(ACTOR, sid, 'send', 'Исследуй систему')
    assert service.run_once()
    assert service.read(ACTOR, sid)['turns'][0]['state'] == 'completed'
    with pg_engine.begin() as connection:
        from sqlalchemy import update
        from bervel.modules.chat.infrastructure.tables import chat_turns
        connection.execute(update(chat_turns).where(chat_turns.c.session_id == sid).values(answer='previously-unknown-secret'))
    reader.redactor.secrets.add('previously-unknown-secret')
    assert 'previously-unknown-secret' not in json.dumps(service.read(ACTOR, sid))
    assert 'previously-unknown-secret' not in json.dumps(service.list_sessions(ACTOR))
    assert 'previously-unknown-secret' not in json.dumps(service.create(ACTOR, 'new'))
    assert 'previously-unknown-secret' not in json.dumps(service.send(ACTOR, sid, 'send', 'Исследуй систему'))
    service.send(ACTOR, sid, 'failure', 'следующий вопрос')
    with repository.claim() as turn:
        receipt = actions.receipts.begin(ACTOR, turn['turn_id'], 0, 'system.read', {'path': 'old'})
        actions.receipts.finish(ACTOR, UUID(receipt['action_id']), result={'kind': 'data', 'operation': 'system.read', 'data': {'text': 'previously-unknown-secret'}})
        service._fail(ACTOR, turn, [], 'Сбой')
    with pg_engine.connect() as connection:
        from sqlalchemy import select
        from bervel.modules.chat.infrastructure.tables import chat_turns
        saved = connection.execute(select(chat_turns.c.results).where(chat_turns.c.session_id == sid)).scalars().all()
        assert 'previously-unknown-secret' not in json.dumps(saved)


def test_ticket_workflow_saves_one_real_draft_and_finishes_with_a_card(pg_engine):
    from bervel.modules.tickets import TicketService
    from bervel.modules.tickets.infrastructure.repository import PostgresTicketRepository
    from bervel.modules.chat.infrastructure.actions import PostgresActionReceipts
    reader = Reader()
    tickets = TicketService(PostgresTicketRepository(pg_engine))
    actions = InspectionActions(reader=reader, receipts=PostgresActionReceipts(pg_engine), tickets=tickets)
    content = {'title': 'Контроль установки', 'problem': 'API_KEY=[СКРЫТО]', 'expected': 'Только чтение', 'actual': '', 'context': '', 'steps': [], 'links': []}
    plans = iter([{'answer': '', 'actions': [{'operation': 'ticket.create', 'arguments': content, 'authorization_quote': 'Создай обращение'}]},
        {'decision': 'allow'}, {'answer': 'Обращение сохранено.', 'actions': []}])
    model = SimpleNamespace(generate=lambda *args, **kwargs: {'answer': json.dumps(next(plans)), 'model': 'test-double'})
    turn = {'turn_id': uuid4(), 'question': 'Создай обращение по проверке', 'workflow': initial_workflow()}
    answer, _, results = InspectionWorkflow(ACTOR, turn, [], [], [], [], actions=actions, memory=None, model=model, checkpoint=lambda state: None).run()
    assert turn['workflow']['phase'] == 'completed'
    assert len(results) == 1 and results[0]['kind'] == 'ticket'
    assert tickets.list(ACTOR)['tickets'][0]['title'] == content['title']
    assert tickets.list(ACTOR)['tickets'][0]['problem'] == 'API_KEY=[СКРЫТО]'


def test_inspection_read_budget_matches_its_published_progress():
    actions, reader, receipts, tickets = setup_actions()
    plan = {'answer': '', 'actions': [{'operation': 'system.read', 'arguments': {'path': 'code.py'}, 'authorization_quote': ''}]}
    plans = iter([plan] * 10 + [{'answer': 'Готово', 'actions': []}])
    model = SimpleNamespace(generate=lambda *args, **kwargs: {'answer': json.dumps(next(plans)), 'model': 'test-double'})
    turn = {'turn_id': uuid4(), 'question': 'Исследуй код', 'workflow': initial_workflow()}
    InspectionWorkflow(ACTOR, turn, [], [], [], [], actions=actions, memory=None, model=model, checkpoint=lambda state: None).run()
    assert turn['workflow']['research_rounds'] == 11
    assert turn['workflow']['max_research_rounds'] is None
    assert turn['workflow']['max_research_seconds'] is None
    assert turn['workflow']['max_web_searches'] == 0
