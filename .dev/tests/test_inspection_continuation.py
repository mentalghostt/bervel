"""Inspection is a durable investigation, with scoped history and explicit effects."""
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.modules.chat.workflow import initial_workflow, advance, research_exhausted
from bervel.modules.inspection.contracts import validate_operation
from bervel.modules.inspection.workflow import InspectionWorkflow
from test_inspection_workflow import ACTOR, setup_actions


def workflow(model, *, history=None, **options):
    actions, reader, receipts, tickets = setup_actions()
    turn = {'turn_id': uuid4(), 'session_id': uuid4(), 'question': 'Исследуй код', 'workflow': initial_workflow()}
    return InspectionWorkflow(ACTOR, turn, history or [], [], [], [], actions=actions, memory=None,
        model=model, checkpoint=lambda state: None, **options), reader, receipts


def test_inspection_continues_after_eight_reads_and_keeps_per_call_timeout():
    calls = []
    def generate(key, prompt, **options):
        calls.append(options)
        plan = {'answer': '', 'actions': [{'operation': 'system.read', 'arguments': {'path': f'{len(calls)}.py'},
            'authorization_quote': ''}]} if len(calls) <= 10 else {'answer': 'Проверено 10 файлов', 'actions': []}
        return {'answer': json.dumps(plan), 'model': 'test'}
    runner, reader, _ = workflow(SimpleNamespace(generate=generate))
    answer, _, _ = runner.run()
    assert len(reader.reads) == 10 and '10 файлов' in answer
    assert runner.turn['workflow']['max_research_rounds'] is None
    assert runner.turn['workflow']['max_research_seconds'] is None
    assert all(call['timeout_seconds'] == 180 for call in calls)


def test_nullable_budget_requires_inspection_marker_and_finite_workflow_stays_finite():
    state = initial_workflow()
    with pytest.raises(CoreError):
        advance(state, max_research_rounds=None, max_research_seconds=None)
    inspection = advance(state, unlimited_research=True, max_research_rounds=None, max_research_seconds=None)
    assert inspection['max_research_rounds'] is None
    assert research_exhausted({**inspection, 'web_searches': 0, 'max_web_searches': 0}) is False
    with pytest.raises(CoreError):
        advance(inspection, unlimited_research=False)
    assert advance(state)['max_research_rounds'] == 4


def test_initial_prompt_contains_relevant_old_answer_and_read_receipt_beyond_last_eight():
    history = [{'turn_id': str(uuid4()), 'state': 'completed', 'question': 'old investigation',
        'answer': 'Сбой registration в auth.py', 'results': [{'kind': 'data', 'operation': 'system.read',
            'data': {'path': 'auth.py', 'text': 'registration failure evidence'}}]}]
    history += [{'turn_id': str(uuid4()), 'state': 'completed', 'question': f'Тема {i}',
        'answer': 'Посторонний ответ', 'results': []} for i in range(20)]
    prompts = []
    def generate(key, prompt, **options):
        prompts.append(prompt)
        return {'answer': json.dumps({'answer': 'готово', 'actions': []}), 'model': 'test'}
    runner, _, _ = workflow(SimpleNamespace(generate=generate), history=history)
    runner.turn['question'] = 'Продолжи registration'
    runner.run()
    assert 'registration failure evidence' in prompts[0]
    assert 'coverage' in prompts[0] and 'conversation.read' in prompts[0]


def test_conversation_read_pages_old_results_with_explicit_coverage():
    from bervel.modules.inspection.context import ConversationContext
    history = [{'turn_id': str(uuid4()), 'state': 'partial', 'question': 'q', 'answer': 'a',
        'results': [{'kind': 'data', 'operation': 'system.read', 'data': {'text': 'Z' * 25000}}]}]
    context = ConversationContext(history)
    entries = []
    offset = 0
    while True:
        page = context.read({'offset': offset, 'limit': 1})
        assert page['coverage']['total_entries'] > 1
        entries += page['entries']
        if page['next_offset'] is None: break
        offset = page['next_offset']
    assert ''.join(entry['text'] for entry in entries if entry['kind'] == 'result').count('Z') == 25000
    assert len(json.dumps(context.select('q'))) < 65000


def test_context_keeps_relevant_tail_excerpt_and_avoids_recursive_conversation_read_echoes():
    from bervel.modules.inspection.context import ConversationContext
    turn = {'turn_id': str(uuid4()), 'state': 'completed', 'question': 'q', 'answer': None, 'results': [
        {'kind': 'data', 'operation': 'system.read', 'data': {'text': 'unrelated ' * 300 + 'needle-tail-evidence'}},
        {'kind': 'data', 'operation': 'conversation.read', 'data': {'request': {'offset': 0, 'limit': 1},
            'entries': [{'text': 'duplicate-echo' * 1000}], 'coverage': {'total_entries': 1}, 'next_offset': None}}]}
    context = ConversationContext([turn])
    selected = json.dumps(context.select('needle-tail-evidence'))
    assert 'needle-tail-evidence' in selected
    raw = json.dumps(context.read({'limit': 20}))
    assert 'duplicate-echo' not in raw
    assert 'request' in raw and 'conversation.read' in raw


@pytest.mark.parametrize('boundary', [1800, 6000])
def test_freshly_known_historical_secret_is_masked_before_fragment_and_excerpt_boundaries(boundary):
    from bervel.modules.inspection.redaction import SecretRedactor
    secret = 'myLaterKnownCredentialQx78ValueTail'
    text = 'contextmarker ' + 'x' * (boundary - 16 - len('contextmarker ')) + secret + ' visible-tail-marker'
    history = [{'turn_id': str(uuid4()), 'state': 'completed', 'question': 'old investigation',
        'answer': text, 'results': []}]
    prompts = []
    model = SimpleNamespace(generate=lambda key, prompt, **kw: prompts.append(prompt) or {
        'answer': json.dumps({'answer': 'done', 'actions': []}), 'model': 'test'})
    runner, reader, _ = workflow(model, history=history)
    # This value was persisted before the reader discovered its credential later.
    reader.redactor = SecretRedactor([secret])
    runner.turn['question'] = 'contextmarker visible-tail-marker'
    context = runner.context()
    page = runner.actions.clean(context.read({'query': 'contextmarker', 'limit': 20}))
    selected = runner.actions.clean(context.select('contextmarker'))
    runner.run()
    for result in (json.dumps(page), json.dumps(selected), *prompts):
        assert secret[:12] not in result
        assert secret[-12:] not in result
    assert 'contextmarker' in prompts[0]
    assert len(prompts[0]) < 28000


def test_context_refreshes_masking_when_a_read_teaches_new_secrets_within_the_turn():
    secret = 'newlyLearnedCredentialAcrossRoundBoundary'
    history = [{'turn_id': str(uuid4()), 'state': 'completed', 'question': 'old contextmarker',
        'answer': 'contextmarker ' + 'x' * 1770 + secret + ' tail', 'results': []}]
    runner, reader, _ = workflow(None, history=history)
    runner.turn['question'] = 'contextmarker'
    original = reader.read
    def read(operation, arguments):
        reader.redactor.secrets.add(secret)
        return original(operation, arguments)
    reader.read = read
    prompts = []
    def generate(key, prompt, **options):
        prompts.append(prompt)
        plan = {'answer': '', 'actions': [{'operation': 'system.read', 'arguments': {'path': '.env'},
            'authorization_quote': ''}]} if len(prompts) == 1 else {'answer': 'done', 'actions': []}
        return {'answer': json.dumps(plan), 'model': 'test'}
    runner.model = SimpleNamespace(generate=generate)
    runner.run()
    assert len(prompts) == 2
    assert secret[:12] not in prompts[1] and secret[-12:] not in prompts[1]


def test_long_history_redacts_whole_values_in_small_batches_before_paging_and_selection():
    from bervel.modules.inspection.redaction import SecretRedactor
    secret = 'longHistoryKnownCredentialAcrossBoundary'
    first = 'historicalfirstmarker ' + 'x' * (6000 - 16 - len('historicalfirstmarker ')) + secret
    history = [{'turn_id': str(uuid4()), 'state': 'completed', 'question': f'Old question {index}',
        'answer': first if index == 0 else ('old unrelated answer\n' * 350),
        'results': [{'kind': 'data', 'operation': 'system.read', 'data': {'path': f'{index}.py',
            'text': f'receipt marker {index}\n' * 350}}]} for index in range(20)]
    assert len(json.dumps(history)) > 100000
    prompts = []
    model = SimpleNamespace(generate=lambda key, prompt, **kw: prompts.append(prompt) or {
        'answer': json.dumps({'answer': 'done', 'actions': []}), 'model': 'test'})
    runner, reader, _ = workflow(model, history=history)
    reader.redactor = SecretRedactor([secret])
    payload_sizes = []
    def bounded_clean(value):
        size = len(json.dumps(value, ensure_ascii=False).encode())
        payload_sizes.append(size)
        if size > 100000:
            raise CoreError('QUOTA_EXCEEDED', 'One redaction response would be too large')
        return reader.redact(value)
    runner.actions.clean = bounded_clean
    runner.turn['question'] = 'historicalfirstmarker'
    context = runner.context()
    entries, offset = [], 0
    while True:
        page = context.read({'offset': offset, 'limit': 20})
        entries.extend(page['entries'])
        if page['next_offset'] is None: break
        offset = page['next_offset']
    assert {entry['turn_id'] for entry in entries} == {item['turn_id'] for item in history}
    assert any('historicalfirstmarker' in entry['text'] for entry in entries)
    serialized = json.dumps(entries)
    assert secret[:12] not in serialized and secret[-12:] not in serialized
    runner.run()
    assert max(payload_sizes) <= 100000 and len(payload_sizes) > 1
    assert len(prompts[0]) < 28000 and 'historicalfirstmarker' in prompts[0]


@pytest.mark.parametrize('operation,arguments', [
    ('test.run', {'suite': 'backend', 'targets': ['.dev/tests/test_example.py'] * 21}),
    ('code.propose', {'title': 'Fix', 'explanation': 'x' * 4001,
        'changes': [{'path': 'a.py', 'content': 'x'}]}),
    ('code.propose', {'title': 'Fix', 'explanation': 'Why',
        'changes': [{'path': f'{index}.py', 'content': 'x'} for index in range(21)]}),
])
def test_model_contract_rejects_values_above_engineering_executor_quotas(operation, arguments):
    with pytest.raises(CoreError): validate_operation(operation, arguments)


@pytest.mark.parametrize('operation,arguments,question,kind', [
    ('test.run', {'suite': 'backend'}, 'Запусти backend тесты', 'inspection_test'),
    ('code.propose', {'title': 'Fix', 'explanation': 'Why', 'changes': [{'path': 'a.py', 'content': 'fixed'}]},
        'Исправь код в a.py', 'code_proposal'),
    ('document.create', {'format': 'txt', 'filename': 'report.txt', 'title': 'Report', 'content': 'Text'},
        'Создай текстовый отчёт', 'file'),
    ('ticket.create', {'title': 'authmodule', 'problem': 'authmodule issue', 'expected': '', 'actual': '',
        'steps': [], 'context': '', 'links': []}, 'Создай обращение по authmodule', 'ticket'),
])
def test_authorization_stays_small_with_long_history_and_only_ticket_uses_relevant_read_facts(operation, arguments, question, kind):
    history = [{'turn_id': str(uuid4()), 'state': 'completed', 'question': 'old topic', 'answer': 'old answer',
        'results': [{'kind': 'data', 'operation': 'system.read', 'data': {'path': f'{index}.py',
            'text': 'targetfact-authmodule line42' if index == 0 else 'unrelated-old-data\n' * 600}}]} for index in range(20)]
    assert len(json.dumps(history)) > 100000
    runner, reader, _ = workflow(None, history=history)
    runner.turn['question'] = question
    runner.actions.engineering = SimpleNamespace(perform=lambda *args: {'kind': kind})
    runner.actions.documents = SimpleNamespace(create=lambda *args: {'kind': kind})
    def bounded_clean(value):
        assert len(json.dumps(value, ensure_ascii=False).encode()) <= 100000
        if operation != 'ticket.create':
            assert not isinstance(value, dict) or 'evidence' not in value
        return reader.redact(value)
    runner.actions.clean = bounded_clean
    prompts = []
    plans = iter([{'answer': '', 'actions': [{'operation': operation, 'arguments': arguments,
        'authorization_quote': question}]}, {'decision': 'allow'}, {'answer': 'done', 'actions': []}])
    def generate(key, prompt, **options):
        if ':authorize:' in key:
            prompts.append(prompt)
        return {'answer': json.dumps(next(plans)), 'model': 'test'}
    runner.model = SimpleNamespace(generate=generate)
    _, _, results = runner.run()
    assert results[0]['kind'] == kind and len(prompts) == 1
    if operation == 'ticket.create':
        assert 'targetfact-authmodule line42' in prompts[0]
        assert 'coverage' in prompts[0] and 'unrelated-old-data' not in prompts[0]
    else:
        assert 'SERVER_READ_RESULTS' not in prompts[0]
        assert 'unrelated-old-data' not in prompts[0]
    assert len(prompts[0]) < 28000


def test_targeted_prompt_keeps_requested_path_and_relevant_evidence_without_unrelated_history():
    history = [{'turn_id': str(uuid4()), 'state': 'completed', 'question': 'Check src/auth.py',
        'answer': 'Failure in auth registration', 'results': [{'kind': 'data', 'operation': 'system.read',
            'data': {'path': 'src/auth.py', 'text': 'target-evidence-marker registration'}}]}]
    history += [{'turn_id': str(uuid4()), 'state': 'completed', 'question': 'Другая тема', 'answer': 'Unrelated ' * 500,
        'results': [{'kind': 'data', 'operation': 'system.read', 'data': {'text': 'irrelevant' * 500}}]} for _ in range(30)]
    prompts = []
    def generate(key, prompt, **options):
        prompts.append(prompt)
        return {'answer': json.dumps({'answer': 'done', 'actions': []}), 'model': 'test'}
    runner, _, _ = workflow(SimpleNamespace(generate=generate), history=history)
    runner.turn['question'] = 'Проверь registration в src/auth.py'
    runner.run()
    assert 'target-evidence-marker' in prompts[0]
    assert 'CURRENT_USER_REQUEST' in prompts[0] and 'src/auth.py' in prompts[0]
    assert 'targeted' in prompts[0].casefold()
    assert 'begin system.list' not in prompts[0]
    assert len(prompts[0]) < 28000


def test_full_small_worker_read_reaches_the_model_last_line_without_a_second_read():
    from pathlib import Path
    content = Path('src/bervel/worker.py').read_text(encoding='utf-8')
    prompts = []
    runner, reader, _ = workflow(None)
    runner.turn['question'] = 'Объясни src/bervel/worker.py'
    def read(operation, arguments):
        reader.reads.append((operation, arguments))
        return {'path': arguments['path'], 'text': content, 'partial': False,
            'line_start': 1, 'line_end': len(content.splitlines()), 'next_offset': None}
    reader.read = read
    def generate(key, prompt, **options):
        prompts.append(prompt)
        if len(prompts) == 1:
            plan = {'answer': '', 'actions': [{'operation': 'system.read',
                'arguments': {'path': 'src/bervel/worker.py'}, 'authorization_quote': ''}]}
        else:
            assert content.splitlines()[-1].strip() in prompt
            assert 'signal.signal(name, handler)' in prompt
            plan = {'answer': 'Объяснение по полному файлу', 'actions': []}
        return {'answer': json.dumps(plan), 'model': 'test'}
    runner.model = SimpleNamespace(generate=generate)
    runner.run()
    assert len(reader.reads) == 1 and len(prompts) == 2


def test_fresh_conversation_read_delivers_the_requested_omitted_fragment_to_next_prompt():
    marker = 'OMITTED_TARGET_FRAGMENT_MARKER'
    history = [{'turn_id': str(uuid4()), 'state': 'completed', 'question': 'Ancient topic',
        'answer': 'Old unrelated line\n' * 160 + marker + '\n' + 'Old unrelated line\n' * 400, 'results': []}]
    runner, reader, _ = workflow(None, history=history)
    runner.turn['question'] = 'Продолжи расследование'
    prompts = []
    def generate(key, prompt, **options):
        prompts.append(prompt)
        if len(prompts) == 1:
            assert marker not in prompt
            plan = {'answer': '', 'actions': [{'operation': 'conversation.read',
                'arguments': {'offset': 1, 'limit': 1}, 'authorization_quote': ''}]}
        else:
            assert marker in prompt
            plan = {'answer': 'Старый фрагмент получен', 'actions': []}
        return {'answer': json.dumps(plan), 'model': 'test'}
    runner.model = SimpleNamespace(generate=generate)
    _, _, results = runner.run()
    assert len(results) == 1 and results[0]['operation'] == 'conversation.read'
    assert marker in results[0]['data']['entries'][0]['text']
    assert not reader.reads and len(prompts) == 2


def test_lexically_relevant_old_fragment_stays_whole_when_it_fits_the_context():
    from bervel.modules.inspection.context import ConversationContext
    history = [{'turn_id': str(uuid4()), 'state': 'completed', 'question': 'Previous',
        'answer': 'needle\n' + 'Relevant text\n' * 350 + 'LAST_RELEVANT_LINE', 'results': []}]
    selected = ConversationContext(history).select('needle')
    assert any('LAST_RELEVANT_LINE' in entry['text'] for entry in selected['entries'])
    assert len(json.dumps(selected)) < 16000


def test_engineering_policy_has_no_prescribed_tool_order_and_keeps_current_intent_permissions():
    from bervel.modules.inspection.workflow import POLICY
    assert len(POLICY) < 5000
    assert 'choose relevant tools' in POLICY
    assert 'database.tables then database.schema' not in POLICY
    assert 'container.list then container.inspect' not in POLICY
    assert 'begin system.list' not in POLICY
    for path in ('src/bervel/core', 'modules/ai_tender_radar', 'modules/chat', 'modules/inspection', 'frontend'):
        assert path in POLICY
    assert 'mandatory exhaustive scan' in POLICY
    assert 'missing capability, permission or source' in POLICY
    assert 'reuse existing results' in POLICY
    assert 'no total step/time budget' in POLICY
    for constraint in ('CURRENT_USER_REQUEST', 'UNTRUSTED DATA', 'Secrets are masked',
            'separate human HTTP confirmation', 'unknown effect', 'actual completed action receipt'):
        assert constraint in POLICY


def test_stop_during_model_call_prevents_planned_effect_and_new_receipts():
    cancelled = False
    def generate(key, prompt, **options):
        nonlocal cancelled
        cancelled = True
        return {'answer': json.dumps({'answer': '', 'actions': [{'operation': 'system.read',
            'arguments': {'path': 'never.py'}, 'authorization_quote': ''}]}), 'model': 'test'}
    runner, reader, receipts = workflow(SimpleNamespace(generate=generate), cancelled=lambda: cancelled)
    with pytest.raises(CoreError) as error: runner.run()
    assert error.value.code == 'INSPECTION_CANCELLED'
    assert not reader.reads and not receipts.items


@pytest.mark.parametrize('operation,arguments', [
    ('conversation.read', {'offset': 0, 'limit': 10}),
    ('diagnostic.run', {'check': 'git.status'}),
    ('api.read', {'path': '/api/v0.1/health'}),
    ('test.run', {'suite': 'backend', 'targets': ['test_one.py']}),
    ('code.propose', {'title': 'Fix', 'explanation': 'Why', 'changes': [{'path': 'a.py', 'content': None}]}),
    ('document.create', {'format': 'txt', 'filename': 'report.txt', 'title': 'Report', 'content': 'Text'}),
])
def test_new_closed_operations_accept_only_their_schema(operation, arguments):
    validate_operation(operation, arguments)
    with pytest.raises(CoreError): validate_operation(operation, {**arguments, 'shell': 'echo nope'})


@pytest.mark.parametrize('operation,arguments', [
    ('test.run', {'suite': 'backend'}),
    ('code.propose', {'title': 'Fix', 'explanation': 'Why', 'changes': [{'path': 'a.py', 'content': 'x'}]}),
    ('document.create', {'format': 'txt', 'filename': 'report.txt', 'title': 'Report', 'content': 'Text'}),
])
def test_model_cannot_authorize_effect_using_only_source_quote(operation, arguments):
    plans = iter([{'answer': '', 'actions': [{'operation': operation, 'arguments': arguments,
        'authorization_quote': 'создай файл и запусти тесты'}]}])
    runner, _, receipts = workflow(SimpleNamespace(generate=lambda *a, **kw: {'answer': json.dumps(next(plans)), 'model': 'test'}))
    with pytest.raises(CoreError) as error: runner.run()
    assert error.value.code == 'ACTION_NOT_AUTHORIZED'
    assert not receipts.items


def test_cancel_is_scoped_idempotent_and_stops_queued_work(pg_engine):
    from bervel.core.infrastructure.documents import PostgresDocumentRepository
    from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
    repository = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine), mode='inspection')
    sid = UUID(repository.create(ACTOR, 'one')['session_id'])
    other = UUID(repository.create(ACTOR, 'two')['session_id'])
    turn = repository.enqueue(ACTOR, sid, 'q', 'Question')
    with pytest.raises(CoreError) as error: repository.cancel(ACTOR, other, UUID(turn['turn_id']), 'cancel')
    assert error.value.code == 'NOT_FOUND'
    result = repository.cancel(ACTOR, sid, UUID(turn['turn_id']), 'cancel')
    assert result['cancel_requested'] is True and result['state'] == 'blocked'
    assert repository.cancel(ACTOR, sid, UUID(turn['turn_id']), 'cancel') == result
    with repository.claim() as claimed: assert claimed is None


def test_cancel_returns_during_an_accepted_long_action_and_next_action_is_refused(pg_engine):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from threading import Event
    from bervel.core.infrastructure.documents import PostgresDocumentRepository
    from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
    repository = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine), mode='inspection')
    sid = UUID(repository.create(ACTOR, 'session')['session_id'])
    repository.enqueue(ACTOR, sid, 'message', 'Запусти тесты')
    started, release = Event(), Event()
    with repository.claim() as turn:
        def long_action():
            with repository.action_guard(ACTOR, sid, turn['turn_id']):
                started.set()
                assert release.wait(5)
        with ThreadPoolExecutor(2) as pool:
            work = pool.submit(long_action)
            assert started.wait(2)
            cancellation = pool.submit(repository.cancel, ACTOR, sid, turn['turn_id'], 'stop')
            try:
                try:
                    stopped = cancellation.result(timeout=2)
                except TimeoutError:
                    pytest.fail('Stop must persist immediately while an accepted operation is running')
                assert stopped['cancel_requested'] and stopped['state'] == 'running'
            finally:
                release.set()
            work.result(timeout=2)
        with pytest.raises(CoreError) as error:
            with repository.action_guard(ACTOR, sid, turn['turn_id']): pytest.fail('Cannot claim another action after Stop')
        assert error.value.code == 'INSPECTION_CANCELLED'


@pytest.mark.parametrize('operation,arguments,question,kind', [
    ('test.run', {'suite': 'backend'}, 'Запусти backend тесты', 'inspection_test'),
    ('code.propose', {'title': 'Fix', 'explanation': 'Why', 'changes': [{'path': 'a.py', 'content': 'fixed'}]},
        'Исправь код в a.py', 'code_proposal'),
    ('document.create', {'format': 'txt', 'filename': 'report.txt', 'title': 'Report', 'content': 'Text'},
        'Создай текстовый отчёт', 'file'),
])
def test_current_authorized_intent_calls_only_the_injected_port(operation, arguments, question, kind):
    runner, _, receipts = workflow(None)
    calls = []
    runner.actions.engineering = SimpleNamespace(perform=lambda *args: calls.append(args) or {'kind': kind})
    runner.actions.documents = SimpleNamespace(create=lambda *args: calls.append(args) or {'kind': kind})
    runner.turn['question'] = question
    plans = iter([{'answer': '', 'actions': [{'operation': operation, 'arguments': arguments,
        'authorization_quote': question}]}, {'decision': 'allow'}, {'answer': 'done', 'actions': []}])
    runner.model = SimpleNamespace(generate=lambda *a, **kw: {'answer': json.dumps(next(plans)), 'model': 'test'})
    _, _, results = runner.run()
    assert results == [{'kind': kind}] and len(calls) == 1
    assert receipts.items[0]['state'] == 'completed'
    if operation != 'document.create':
        assert calls[0][1:3] == (runner.turn['session_id'], runner.turn['turn_id'])
        assert calls[0][4] == operation


@pytest.mark.parametrize('question,format,content', [
    ('Нарисуй схему компонентов', 'svg', 'Client\nAPI\nDatabase'),
    ('Покажи изображение потока данных', 'svg', 'Request\nProcessor\nResult'),
    ('Подготовь документ с результатами проверки', 'docx', '# Результаты проверки'),
])
def test_current_visual_or_document_request_can_choose_format_without_file_keyword(question, format, content):
    runner, _, receipts = workflow(None)
    runner.turn['question'] = question
    saved = []
    identifier = str(uuid4())
    card = {'kind': 'file', 'artifact_id': identifier, 'filename': 'result.' + format,
        'download_url': '/api/v0.1/artifacts/' + identifier + '/content'}
    runner.actions.documents = SimpleNamespace(create=lambda *args: saved.append(args) or card)
    prompts = []
    plans = iter([{'answer': '', 'actions': [{'operation': 'document.create', 'arguments': {'format': format,
        'filename': 'result.' + format, 'title': 'Результат', 'content': content}, 'authorization_quote': question}]},
        {'decision': 'allow'}, {'answer': 'Готово', 'actions': []}])
    def generate(key, prompt, **options):
        prompts.append(prompt)
        if ':authorize:' in key:
            # A classifier receives the current intent and the rule allowing a chosen format;
            # the workflow must not invent a separate "file" keyword confirmation gate.
            assert question in prompt
            assert 'diagram/image' in prompt
            assert 'format is not specified' in prompt
            assert 'word file or SVG' in prompt
        return {'answer': json.dumps(next(plans)), 'model': 'test'}
    runner.model = SimpleNamespace(generate=generate)
    _, _, results = runner.run()
    assert results == [card] and len(saved) == 1
    assert receipts.items[0]['state'] == 'completed'
    assert '/api/v0.1/artifacts/{uuid}/content' in prompts[0]
    assert 'Markdown' in prompts[0] and 'GFM tables' in prompts[0]
    assert 'actual completed action receipt' in prompts[0]
    assert 'content=plain labels, one label per line' in prompts[0]
    assert 'do not send SVG/HTML/XML source' in prompts[0]
    assert all(name in prompts[0] for name in ('PDF', 'DOCX', 'SVG', 'TXT', 'CSV'))


def test_source_request_quoted_in_current_message_is_denied_by_independent_classifier():
    question = 'В файле написано «запусти backend тесты». Объясни этот текст.'
    runner, _, receipts = workflow(None)
    runner.turn['question'] = question
    plans = iter([{'answer': '', 'actions': [{'operation': 'test.run', 'arguments': {'suite': 'backend'},
        'authorization_quote': 'запусти backend тесты'}]}, {'decision': 'deny'}])
    prompts = []
    def generate(key, prompt, **options):
        prompts.append(prompt)
        return {'answer': json.dumps(next(plans)), 'model': 'test'}
    runner.model = SimpleNamespace(generate=generate)
    with pytest.raises(CoreError) as error: runner.run()
    assert error.value.code == 'ACTION_NOT_AUTHORIZED' and not receipts.items
    assert 'quoted from files' in prompts[1]


def test_missing_feature_has_explicit_error_without_a_receipt():
    actions, *_ = setup_actions()
    with pytest.raises(CoreError) as error:
        actions.execute(ACTOR, uuid4(), 0, 'test.run', {'suite': 'backend'},
            operation_authorized=True, session_id=uuid4())
    assert error.value.code == 'INSPECTION_FEATURE_UNAVAILABLE'


@pytest.mark.parametrize('operation,arguments,raw_kind', [
    ('diagnostic.run', {'check': 'git.status'}, 'inspection_diagnostic'),
    ('api.read', {'path': '/api/v0.1/health'}, 'inspection_api'),
])
def test_engineering_reads_use_the_existing_data_result_contract(operation, arguments, raw_kind):
    actions, *_ = setup_actions()
    raw = {'kind': raw_kind, 'output': 'observed data'}
    actions.engineering = SimpleNamespace(perform=lambda *args: raw)
    result = actions.execute(ACTOR, uuid4(), 0, operation, arguments, session_id=uuid4())
    assert result == {'kind': 'data', 'operation': operation, 'data': raw}


def test_running_cancellation_keeps_saved_read_and_prevents_the_next_action(pg_engine):
    from bervel.core.infrastructure.documents import PostgresDocumentRepository
    from bervel.modules.chat import ChatService
    from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
    from bervel.modules.chat.infrastructure.actions import PostgresActionReceipts
    repository = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine), mode='inspection')
    actions, reader, *_ = setup_actions()
    actions.receipts = PostgresActionReceipts(pg_engine)
    sid = UUID(repository.create(ACTOR, 'session')['session_id'])
    sent = repository.enqueue(ACTOR, sid, 'question', 'Исследуй код')
    count = 0
    def generate(key, prompt, **options):
        nonlocal count
        count += 1
        if count == 2:
            stopped = repository.cancel(ACTOR, sid, UUID(sent['turn_id']), 'stop')
            assert stopped['state'] == 'running' and stopped['cancel_requested']
        return {'answer': json.dumps({'answer': '', 'actions': [{'operation': 'system.read',
            'arguments': {'path': f'{count}.py'}, 'authorization_quote': ''}]}), 'model': 'test'}
    service = ChatService(repository, None, SimpleNamespace(generate=generate), actions=actions, workflow=InspectionWorkflow)
    assert service.run_once()
    turn = service.read(ACTOR, sid)['turns'][0]
    assert turn['state'] == 'blocked' and turn['cancel_requested']
    assert len(turn['results']) == 1 and reader.reads == [('system.read', {'path': '1.py'})]
    assert 'остановлена' in turn['answer'].casefold()


def test_service_explains_explicit_unavailable_feature_instead_of_generic_model_failure(pg_engine):
    from bervel.core.infrastructure.documents import PostgresDocumentRepository
    from bervel.modules.chat import ChatService
    from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
    from bervel.modules.chat.infrastructure.actions import PostgresActionReceipts
    repository = PostgresChatRepository(pg_engine, PostgresDocumentRepository(pg_engine), mode='inspection')
    actions, *_ = setup_actions()
    actions.receipts = PostgresActionReceipts(pg_engine)
    sid = UUID(repository.create(ACTOR, 'session')['session_id'])
    repository.enqueue(ACTOR, sid, 'message', 'Запусти backend тесты')
    plans = iter([{'answer': '', 'actions': [{'operation': 'test.run', 'arguments': {'suite': 'backend'},
        'authorization_quote': 'Запусти backend тесты'}]}, {'decision': 'allow'}])
    model = SimpleNamespace(generate=lambda *a, **kw: {'answer': json.dumps(next(plans)), 'model': 'test'})
    service = ChatService(repository, None, model, actions=actions, workflow=InspectionWorkflow)
    assert service.run_once()
    turn = service.read(ACTOR, sid)['turns'][0]
    assert turn['state'] == 'blocked' and 'Инженерные операции сейчас недоступны' in turn['answer']


def test_cancel_http_route_is_inspection_only_and_command_key_is_validated(pg_engine, tmp_path, monkeypatch):
    from bervel.app_setup import create_app
    from bervel.core.config import Settings
    from http_client import ASGITestClient
    from bervel.modules.inspection.infrastructure.client import InspectionClient
    monkeypatch.setattr(InspectionClient, 'redact', lambda self, value: value)
    settings = Settings(pg_engine.url.render_as_string(hide_password=False), tmp_path / 'objects', {'a' * 32: ACTOR})
    headers = {'Authorization': 'Bearer ' + 'a' * 32}
    with ASGITestClient(create_app(settings)) as http:
        response = http.post('/api/v0.1/inspection/sessions', headers=headers, json={'command_key': 'session'})
        path = '/api/v0.1/inspection/sessions/' + response.json()['session_id']
        turn = http.post(path + '/messages', headers=headers, json={'command_key': 'message', 'content': 'Investigate'}).json()
        assert turn['cancel_requested'] is False
        stop = path + '/messages/' + turn['turn_id'] + '/cancel'
        assert http.post(stop, headers=headers, json={}).status_code == 422
        stopped = http.post(stop, headers=headers, json={'command_key': 'stop'})
        assert stopped.status_code == 200
        assert stopped.json()['cancel_requested'] and stopped.json()['state'] == 'blocked'
        assert http.get(path, headers=headers).json()['turns'][0]['cancel_requested'] is True
        assert http.post(stop.replace('/inspection/', '/chat/'), headers=headers,
            json={'command_key': 'stop'}).status_code == 404

