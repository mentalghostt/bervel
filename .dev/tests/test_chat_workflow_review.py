"""Independent workflow review reproducers; real storage/actions/HTTP/renderers."""
import json
from uuid import UUID

from bervel.core.errors import CoreError
from test_assistant_resilience import document, http_chat, request_payload
from test_assistant_workflows import content
from test_chat_long_tasks import FACT, QUESTION, action, response, seed


def test_research_authorization_timeout_still_uses_reserved_synthesis_budget(services, pg_engine):
    actor, reference = seed(services)
    synthesis_calls = []

    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                if value['PROPOSED_OPERATION'] == 'web.search':
                    raise CoreError('MODEL_TIMEOUT', 'private upstream timeout')
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if value['WORKFLOW_STATE']['phase'] == 'synthesizing':
                synthesis_calls.append(request_id)
                return response([action('file.create', document(content=FACT + '\nSource: production.txt, line 1'), QUESTION)])
            if not value['ACTION_RESULTS']:
                return response([action('file.read_page', {'artifact_id': str(reference.artifact_id)})])
            return response([action('web.search', {'query': 'Zeta production'}, QUESTION)])

    with http_chat(services, pg_engine, Model(), actor=actor) as (_, service, _, _, send, read):
        send(QUESTION)
        assert service.run_once()
        turn = read()
        assert synthesis_calls, 'A research authorization timeout must not discard the reserved synthesis/generation phases'
        assert turn['state'] == 'partial'
        assert any(card['kind'] == 'file' and card['filename'].endswith('.pdf') for card in turn['results'])
        assert 'private upstream' not in str(turn)


def test_generation_correction_cannot_silently_change_the_requested_pdf_format(services, pg_engine):
    task = {'deliverable': 'file', 'formats': ['pdf'], 'research_required': False}
    question = 'Создай PDF с текстом User supplied production facts.'

    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            args = document(filename='../analysis.pdf') if not value['ACTION_RESULTS'] else document(format='txt', filename='analysis.txt')
            return response([action('file.create', args, 'Создай PDF')], task=task)

    with http_chat(services, pg_engine, Model()) as (_, service, _, actor, send, read):
        send(question)
        assert service.run_once()
        turn = read()
        files = [card for card in turn['results'] if card['kind'] == 'file']
        assert all(card['filename'].endswith('.pdf') for card in files), 'Correction must obey the same fixed goal format as the initial draft'
        assert all(item['filename'].endswith('.pdf') for item in services[0].list(actor)['artifacts'])


def test_missing_one_of_two_requested_file_formats_is_not_completed(services, pg_engine):
    task = {'deliverable': 'file', 'formats': ['pdf', 'docx'], 'research_required': False}

    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            return response([action('file.create', document(), 'Создай PDF и DOCX')], task=task)

    with http_chat(services, pg_engine, Model()) as (_, service, _, _, send, read):
        send('Создай PDF и DOCX с текстом User supplied production facts.')
        assert service.run_once()
        turn = read()
        suffixes = {card['filename'].rsplit('.', 1)[-1] for card in turn['results'] if card['kind'] == 'file'}
        assert turn['state'] != 'completed' or suffixes == {'pdf', 'docx'}, 'Completed must account for every requested output format'


def test_a_saved_but_unverified_empty_text_is_blocked_not_partial(services, pg_engine):
    task = {'deliverable': 'file', 'formats': ['txt'], 'research_required': False}

    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            return response([action('file.create', document(format='txt', filename='analysis.txt', content='   '), 'Создай TXT')], task=task)

    with http_chat(services, pg_engine, Model()) as (_, service, _, _, send, read):
        send('Создай TXT с текстом Production facts.')
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'blocked', 'A failed local verification cannot establish a useful partial document'
        assert turn['workflow']['phase'] == 'blocked'


def test_interrupted_unknown_effect_has_a_blocked_terminal_workflow_without_resume(services, pg_engine):
    class Model:
        def generate(self, *_args, **_options):
            raise AssertionError('Interrupted work must never resume a model request')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        sent = send('Создай PDF с текстом Production facts.')
        with service.repository.claim() as claimed:
            assert not claimed['interrupted']
            actions.receipts.begin(actor, UUID(sent['turn_id']), 0, 'file.create', document())
        assert service.run_once()
        turn = read()
        assert turn['actions'][0]['state'] == 'unknown'
        assert turn['state'] == 'blocked' and turn['workflow']['phase'] == 'blocked', 'An interrupted unknown effect needs the explicit blocked outcome and terminal phase'
        assert services[0].list(actor)['artifacts'] == []
        assert not service.run_once()


def test_notebook_budget_counts_json_escaping_in_the_actual_serialized_entries():
    from bervel.modules.chat.workflow import initial_workflow, remember
    state = initial_workflow()
    remember(state, {'kind': 'web', 'answer': '"' * 32000,
        'sources': [{'title': 'Production', 'url': 'https://source.example/production'}]}, 'web.search', 0)
    used = len(json.dumps(state['evidence']['entries'], ensure_ascii=False, separators=(',', ':')).encode())
    assert used <= 32 * 1024, 'The web evidence allocation must stay within 32 KiB after JSON escaping'


def test_repeated_completed_web_search_stops_collection_but_preserves_file_delivery(services, pg_engine):
    web_calls = []

    class Model:
        def generate(self, request_id, prompt, **options):
            if options.get('web_search'):
                web_calls.append(request_id)
                return {'answer': 'Zeta has 3 forging lines. https://source.example/production', 'model': 'fixture',
                    'sources': [{'title': 'Zeta', 'url': 'https://source.example/production'}]}
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            if value['WORKFLOW_STATE']['phase'] == 'synthesizing':
                return response([action('file.create', document(content='Zeta has 3 forging lines.\nhttps://source.example/production'), QUESTION)])
            return response([action('web.search', {'query': 'Zeta production'}, QUESTION)])

    with http_chat(services, pg_engine, Model()) as (_, service, _, _, send, read):
        send(QUESTION)
        assert service.run_once()
        turn = read()
        assert len(web_calls) == 1, 'A completed paid search must never be repeated'
        assert turn['state'] == 'partial' and any(card['kind'] == 'file' for card in turn['results']), \
            'Stopping duplicate research must still use the known evidence and reserved file-delivery budget'


def test_ordinary_create_read_edit_stays_generating_until_local_verification(services, pg_engine):
    task = {'deliverable': 'operation', 'formats': [], 'research_required': False}
    question = 'Создай обращение, перечитай его и измени фактический результат на Пустой файл.'
    planner_phases, read_turn = [], None
    authorization_phases = []

    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                authorization_phases.append(read_turn()['workflow']['phase'])
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            planner_phases.append(value['WORKFLOW_STATE']['phase'])
            results = value['ACTION_RESULTS']
            if not results:
                return response([action('ticket.create', content(), question)], task=task)
            ticket_id = results[0]['ticket_id']
            if len(results) == 1:
                return response([action('ticket.read', {'ticket_id': ticket_id})], task=task)
            if len(results) == 2:
                args = {**content(), 'ticket_id': ticket_id, 'expected_revision': results[1]['data']['revision'], 'actual': 'Пустой файл'}
                return response([action('ticket.edit', args, question)], task=task)
            return response([], task=task, answer='Изменения сохранены.')

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        read_turn = read
        send(question)
        assert service.run_once()
        turn = read()
        assert turn['state'] == 'completed'
        saved = actions.tickets.read(actor, UUID(turn['results'][0]['ticket_id']))
        assert saved['revision'] == 2 and saved['actual'] == 'Пустой файл'
        assert 'verifying' not in planner_phases + authorization_phases, \
            'Verification is local and terminal: further model calls and user effects must stay in generating'
        assert [entry['phase'] for entry in turn['workflow']['history']] == ['research', 'generating', 'verifying', 'completed']


def test_notebook_marks_omitted_web_source_links_as_partial_coverage():
    from bervel.modules.chat.workflow import initial_workflow, remember
    state = initial_workflow()
    sources = [{'title': f'Factory {number}', 'url': f'https://source.example/factory-{number}'} for number in range(20)]
    remember(state, {'kind': 'web', 'answer': 'Factories use multiple forging lines; full output figures are not published.',
        'sources': sources}, 'web.search', 0)
    notebook = state['evidence']
    entry = notebook['entries'][0]
    assert len(entry['sources']) == len(sources) or entry['truncated'] or notebook['coverage']['truncated'], \
        'Omitted provenance links must never be presented as complete notebook evidence'


def test_ordinary_operation_read_stage_obeys_the_same_four_round_research_budget(services, pg_engine):
    task = {'deliverable': 'operation', 'formats': [], 'research_required': False}
    planning_calls = []

    class Model:
        def generate(self, request_id, prompt, **options):
            value = request_payload(prompt)
            if 'AUTHORIZATION_REQUEST' in value:
                return {'answer': '{"decision":"allow"}', 'model': 'fixture'}
            planning_calls.append(request_id)
            return response([action('ticket.list', {})], task=task)

    with http_chat(services, pg_engine, Model()) as (_, service, actions, actor, send, read):
        actions.tickets.create(actor, content(), 'existing-ticket')
        send('Прочитай список обращений.')
        assert service.run_once()
        turn = read()
        assert len(turn['actions']) <= 4 and len(planning_calls) <= 4, 'An operation goal must not bypass the research-round cap'
        assert turn['workflow']['research_rounds'] == turn['workflow']['max_research_rounds'] == 4
        assert turn['state'] in {'partial', 'blocked'}
        assert not service.run_once()


def test_interrupted_after_terminal_checkpoint_does_not_leave_the_queue_stuck(services, pg_engine):
    from bervel.modules.chat.workflow import advance

    class Model:
        def generate(self, *_args, **_options):
            raise AssertionError('Recovering a terminal checkpoint must never invoke a model')

    with http_chat(services, pg_engine, Model()) as (_, service, _, _, send, read):
        send('Создай PDF с текстом Production facts.')
        with service.repository.claim() as claimed:
            # This is the durable first half of ChatService._fail before its separate finish.
            service.repository.checkpoint(claimed['turn_id'], advance(claimed['workflow'], 'blocked', 'Исполнение прервано.'))
        assert service.run_once(), 'An interrupted terminal checkpoint must be finalized without trying to advance its phase'
        turn = read()
        assert turn['state'] == 'blocked' and turn['workflow']['phase'] == 'blocked'
        assert not service.run_once()
