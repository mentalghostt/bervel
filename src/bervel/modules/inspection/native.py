"""Native Codex execution: input and context belong to the CLI, not a planner."""
from time import sleep
from uuid import UUID

from bervel.core.errors import CoreError
from bervel.core.models import ArtifactRef
from bervel.modules.chat import advance_workflow, file_card


def native_actions(items: list[dict]) -> list[dict]:
    actions = []
    for item in items:
        kind = item.get('type')
        if kind not in {'command_execution', 'file_change', 'web_search', 'mcp_tool_call'}:
            continue
        state = {'completed': 'completed', 'failed': 'failed', 'in_progress': 'running'}.get(item.get('status'), 'running')
        if kind == 'command_execution':
            operation, arguments = 'command.run', {'command': item.get('command', '')}
            result = {'kind': 'native_command', 'output': item.get('aggregated_output', ''), 'exit_code': item.get('exit_code')}
            if item.get('exit_code') not in (None, 0): state = 'failed'
        elif kind == 'file_change':
            operation, arguments = 'code.change', {}
            result = {'kind': 'native_changes', 'files': [change['path'] for change in item.get('changes', []) if isinstance(change, dict) and isinstance(change.get('path'), str)],
                'changes': item.get('changes', [])}
        elif kind == 'web_search':
            operation, arguments = 'web.search', {'query': item.get('query', '')}
            result = {'kind': 'native_search', 'query': item.get('query', '')}
        else:
            operation, arguments = 'tool.call', {'server': item.get('server', ''), 'tool': item.get('tool', '')}
            result = {'kind': 'native_tool', 'result': item.get('result')}
        actions.append({'action_id': 'cli:' + str(item.get('id', len(actions))), 'ordinal': len(actions),
            'operation': operation, 'arguments': arguments, 'state': state, 'result': result,
            'error': {'code': 'NATIVE_TOOL_FAILED', 'message': 'Команда завершилась ошибкой.'} if state == 'failed' else None})
    return actions


def progress_text(job: dict) -> str:
    if isinstance(job.get('progress'), str) and job['progress'].strip(): return job['progress']
    for item in reversed(job.get('items') or []):
        kind = item.get('type')
        if kind == 'command_execution': return ('Выполняется: ' if item.get('status') == 'in_progress' else 'Команда: ') + item.get('command', '')
        if kind == 'file_change': return 'Изменения файлов: ' + ', '.join(change.get('path', '') for change in item.get('changes', []))
        if kind == 'web_search': return 'Поиск в интернете: ' + item.get('query', '')
        if kind == 'agent_message' and item.get('text'): return item['text']
    return {'queued': 'Codex CLI ожидает выполнения.', 'running': 'Codex CLI выполняет запрос.',
        'completed': 'Codex CLI завершил запрос.', 'cancelled': 'Выполнение остановлено.',
        'unknown': 'Исход выполнения неизвестен.', 'failed': 'Codex CLI сообщил об ошибке.'}.get(job.get('state'), 'Ожидание Codex CLI.')


class NativeInspectionWorkflow:
    native_context = True
    supports_cancellation = True

    def __init__(self, client, artifacts):
        self.client, self.artifacts = client, artifacts

    def run(self, actor, turn, *, checkpoint, cancelled, inbox=None):
        state = turn['workflow']
        def publish_native(native):
            nonlocal state
            if native != state.get('native'):
                following = advance_workflow(state, native=native, unlimited_research=True,
                    max_research_rounds=None, max_research_seconds=None)
                checkpoint(following)
                state = following
                turn['workflow'] = state
        def publish(job):
            previous = state.get('native') or {}
            native = {'engine': 'codex-cli', 'thread_id': job.get('thread_id') or previous.get('thread_id'),
                'state': job['state'], 'progress_text': progress_text(job), 'actions': native_actions(job.get('items') or []),
                'error_code': job.get('error_code')}
            publish_native(native)
        def connection_warning():
            native = {**(state.get('native') or {}), 'engine': 'codex-cli', 'state': 'running',
                'progress_text': 'Связь с Codex CLI восстанавливается. Исполнение может продолжаться; запрос остановки будет доставлен после восстановления связи.',
                'error_code': 'INSPECTION_CLI_TRANSPORT'}
            try:
                publish_native(native)
            except CoreError as error:
                # An output-filter outage must not mark a live CLI job terminal.
                # Keep the durable running state and retry observation/cancellation.
                if error.code == 'STALE_ATTEMPT': raise
        def reconcile(job):
            messages = inbox.pending_followups(actor, turn['session_id'], turn['turn_id'])
            receipts = {item['message_id']: item['state'] for item in job.get('followups', [])}
            remaining = []
            for message in messages:
                receipt = receipts.get(str(message['turn_id']))
                if receipt in {'accepted', 'rejected', 'unknown'}:
                    inbox.record_followup_delivery(actor, turn['session_id'], turn['turn_id'], message['turn_id'], receipt)
                else:
                    remaining.append((message, receipt))
            return remaining
        def deliver(job):
            if inbox is None:
                return job
            remaining = reconcile(job)
            if not remaining or cancelled():
                return job
            message, receipt = remaining[0]
            if receipt in {'queued', 'sending'}:
                return job
            if job['state'] not in {'queued', 'running'} and message['delivery_state'] != 'sending':
                return job
            if not inbox.mark_followup_sending(actor, turn['session_id'], turn['turn_id'], message['turn_id']):
                return job
            # The same durable ID recovers both a lost POST response and a worker
            # restart. The runner owns deduplication and never repeats uncertain RPC.
            try:
                following = self.client.steer(actor, turn, message)
            except CoreError as error:
                if error.code != 'INSPECTION_CLI_MESSAGE_REJECTED':
                    raise
                # A refused HTTP command is not the CLI's definite no-active-turn
                # receipt. Never replay it as a new turn or interrupt the parent.
                inbox.record_followup_delivery(actor, turn['session_id'], turn['turn_id'], message['turn_id'], 'unknown')
                return job
            reconcile(following)
            return following
        if cancelled() and not turn.get('interrupted'):
            turn['workflow'] = advance_workflow(state, 'blocked', 'Остановлено пользователем.')
            return 'Выполнение остановлено пользователем.', None, []
        operation = 'read' if turn.get('interrupted') else 'start'
        cancellation_sent = False
        delay = self.client.poll_interval
        while True:
            try:
                job = getattr(self.client, operation)(actor, turn)
                if operation == 'cancel': cancellation_sent = True
                job = deliver(job)
            except CoreError as error:
                if error.code == 'INSPECTION_CLI_NOT_FOUND':
                    if operation != 'read':
                        operation = 'read'
                        continue
                    job = {'state': 'unknown', 'error_code': 'NATIVE_JOB_NOT_FOUND', 'items': [], 'exports': []}
                elif error.code == 'INSPECTION_CLI_TRANSPORT':
                    connection_warning()
                    sleep(delay)
                    delay = min(5, delay * 2)
                    # A lost POST acknowledgement is recovered only by GET. Stop is
                    # idempotent and retried until its acknowledgement is received.
                    operation = 'cancel' if cancelled() and not cancellation_sent else 'read'
                    continue
                else:
                    raise
            try:
                publish(job)
            except CoreError as error:
                if error.code == 'STALE_ATTEMPT': raise
                connection_warning()
                sleep(delay)
                delay = min(5, delay * 2)
                operation = 'cancel' if cancelled() and not cancellation_sent else 'read'
                continue
            delay = self.client.poll_interval
            status = job['state']
            if status in {'completed', 'cancelled', 'failed', 'unknown'}:
                cards, invalid_exports = [], False
                seen = set()
                for exported in job.get('exports') or []:
                    try:
                        identifier = UUID(exported['artifact_id'])
                        if identifier in seen: continue
                        seen.add(identifier)
                        if self.artifacts is None: raise ValueError('Artifact metadata unavailable')
                        cards.append(file_card(self.artifacts.metadata(actor, ArtifactRef(identifier))))
                    except (CoreError, KeyError, TypeError, ValueError):
                        invalid_exports = True
                answer = job.get('answer') or ''
                if status == 'cancelled': answer = (answer + '\n\n' if answer else '') + 'Выполнение остановлено пользователем.'
                if status in {'failed', 'unknown'}:
                    message = ('Исполнение Codex CLI прервано; исход неизвестен. Автоматический повтор не выполняется.' if status == 'unknown'
                        else 'Codex CLI не завершил запрос: ' + str(job.get('error_code') or 'NATIVE_FAILED') + '.')
                    answer = (answer + '\n\n' if answer else '') + message
                if invalid_exports: answer += '\n\nЧасть вложений не подтверждена файловым сервисом и не добавлена к ответу.'
                phase = 'blocked' if status != 'completed' else 'partial' if invalid_exports else 'completed'
                turn['workflow'] = advance_workflow(state, phase)
                return answer, job.get('model'), cards
            if status not in {'queued', 'running'}:
                raise CoreError('INSPECTION_CLI_INVALID_RESPONSE', 'Codex CLI вернул неизвестное состояние.')
            if cancelled() and not cancellation_sent:
                operation = 'cancel'
            else:
                sleep(self.client.poll_interval)
                operation = 'read'
