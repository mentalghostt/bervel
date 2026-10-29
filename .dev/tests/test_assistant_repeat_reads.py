"""Read refreshes may repeat; effects may not, and planning remains bounded."""
import json
from uuid import UUID, uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.modules.chat import ChatService
from test_assistant_workflows import actor_with_chat, build_actions, content


def planned(operation, arguments):
    return {'answer': json.dumps({'answer': '', 'actions': [{'operation': operation, 'arguments': arguments,
        'authorization_quote': 'Контрольное обращение'}]}), 'model': 'fixture'}


def is_authorization(prompt):
    return 'AUTHORIZATION_REQUEST' in json.loads(prompt.split('\n', 1)[1])


def test_read_edit_reread_observes_actual_saved_revision_in_one_turn(services, pg_engine):
    actions = build_actions(services, pg_engine)
    actor = actor_with_chat(services)
    ticket = actions.tickets.create(actor, content(), 'seed')
    identifier = ticket['ticket_id']
    class Model:
        def generate(self, request_id, prompt, **options):
            if is_authorization(prompt): return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            results = json.loads(prompt.split('\n', 1)[1])['ACTION_RESULTS']
            if not results:
                return planned('ticket.read', {'ticket_id': identifier})
            if len(results)==1:
                assert results[0]['_receipt']['operation']=='ticket.read' and results[0]['_receipt']['state']=='completed'
                assert results[0]['_receipt']['result_ref']['revision']==1
                assert results[0]['data']['revision']==1
                return planned('ticket.edit', {**content(), 'actual': 'Обновлено и перечитано', 'ticket_id': identifier, 'expected_revision': 1})
            if len(results)==2:
                assert results[1]['_receipt']['operation']=='ticket.edit' and results[1]['_receipt']['state']=='completed'
                assert results[1]['_receipt']['arguments']['actual']=='Обновлено и перечитано'
                assert results[1]['_receipt']['result_ref']['ticket_id']==identifier
                return planned('ticket.read', {'ticket_id': identifier})
            assert results[2]['data']['revision']==2 and results[2]['data']['actual']=='Обновлено и перечитано'
            return {'answer': '{"answer":"Изменение проверено","actions":[]}', 'model': 'fixture'}
    turn = {'turn_id': uuid4(), 'question': 'Контрольное обращение: прочитай, измени фактическое поведение и перечитай для проверки.'}
    service = ChatService(None, None, Model(), actions=actions)
    _, _, results = service._assistant(actor, turn, [], [], [], [])
    assert results[-1]['data']['revision']==2
    assert all('_receipt' not in result for result in results)
    assert actions.tickets.read(actor, UUID(identifier))['actual']=='Обновлено и перечитано'
    receipts = actions.receipts.read(actor, turn['turn_id'])
    assert [row['operation'] for row in receipts]==['ticket.read', 'ticket.edit', 'ticket.read']
    assert all(row['state']=='completed' for row in receipts)
    assert all('_receipt' not in row['result'] for row in receipts)


def test_duplicate_mutation_still_stops_before_creating_second_ticket(services, pg_engine):
    actions = build_actions(services, pg_engine)
    actor = actor_with_chat(services)
    class Model:
        def generate(self, request_id, prompt, **options):
            if is_authorization(prompt): return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            return planned('ticket.create', content())
    turn = {'turn_id': uuid4(), 'question': 'Контрольное обращение: создай одну карточку.'}
    service = ChatService(None, None, Model(), actions=actions)
    with pytest.raises(CoreError) as error:
        service._assistant(actor, turn, [], [], [], [])
    assert error.value.code=='ASSISTANT_INVALID_PLAN'
    assert len(actions.tickets.list(actor)['tickets'])==1
    assert len(actions.receipts.read(actor, turn['turn_id']))==1


def test_repeated_read_loop_stops_after_four_research_rounds_without_effects(services, pg_engine):
    actions = build_actions(services, pg_engine)
    actor = actor_with_chat(services)
    ticket = actions.tickets.create(actor, content(), 'seed')
    class Model:
        def generate(self, request_id, prompt, **options):
            if is_authorization(prompt): return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            return planned('ticket.read', {'ticket_id': ticket['ticket_id']})
    turn = {'turn_id': uuid4(), 'question': 'Контрольное обращение: проверь текущее состояние.'}
    service = ChatService(None, None, Model(), actions=actions)
    with pytest.raises(CoreError) as error:
        service._assistant(actor, turn, [], [], [], [])
    assert error.value.code=='ASSISTANT_STEP_LIMIT'
    receipts = actions.receipts.read(actor, turn['turn_id'])
    assert len(receipts)==4 and all(row['operation']=='ticket.read' and row['state']=='completed' for row in receipts)
    assert actions.tickets.read(actor, UUID(ticket['ticket_id']))==ticket
