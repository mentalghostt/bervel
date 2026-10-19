import json
import re
import socket
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bervel.core.errors import CoreError
from bervel.core.process_models import EngineExecution, WorkflowDefinition


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


class N8nEngine:
    def __init__(self, base_url, api_key, webhook_key, workflow_id, webhook_path, timeout=15):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not 1 <= timeout <= 120):
            raise ValueError('Invalid n8n connection configuration')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', workflow_id) or not re.fullmatch(r'[A-Za-z0-9/_-]{1,128}', webhook_path):
            raise ValueError('Invalid n8n workflow identity')
        if not api_key or not webhook_key or any('\n' in key or '\r' in key for key in (api_key, webhook_key)):
            raise ValueError('Invalid n8n authentication configuration')
        self.base_url, self.api_key, self.webhook_key = base_url.rstrip('/'), api_key, webhook_key
        self.workflow_id, self.webhook_path, self.timeout = workflow_id, webhook_path, timeout
        self.client = build_opener(NoRedirect())

    def request(self, method, path, payload=None, *, webhook=False):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        headers.update({'Authorization': 'Bearer ' + self.webhook_key} if webhook else {'X-N8N-API-KEY': self.api_key})
        request = Request(self.base_url + path, data=json.dumps(payload).encode() if payload is not None else None,
                          method=method, headers=headers)
        try:
            with self.client.open(request, timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
                if len(raw) > 8 * 1024 * 1024:
                    raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Engine response exceeds the supported limit')
                value = json.loads(raw) if raw else {}
                if not isinstance(value, dict):
                    raise ValueError('Expected engine response object')
                return value
        except HTTPError as error:
            code = 'ENGINE_RESPONSE_UNKNOWN' if error.code == 404 else 'TRANSIENT_DEPENDENCY_ERROR'
            raise CoreError(code, 'Engine request failed; reconcile before repeating an operation') from error
        except (URLError, TimeoutError, socket.timeout, ConnectionError, ValueError) as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Engine response is unavailable; operation outcome may be unknown') from error

    def describe(self):
        workflow = self.request('GET', '/api/v1/workflows/' + quote(self.workflow_id, safe=''))
        version = workflow.get('activeVersionId')
        if not workflow.get('active') or not isinstance(version, str) or not version:
            raise CoreError('WORKFLOW_MISMATCH', 'A published workflow version is required')
        return WorkflowDefinition(self.workflow_id, version, '1.0.0')

    def start(self, process_id, invocation_token):
        self.request('POST', '/webhook/' + self.webhook_path,
                     {'process_run_id': process_id, 'invocation_token': invocation_token}, webhook=True)

    def _execution(self, value):
        try:
            payload = (value.get('data') or {}).get('resultData', {}).get('runData', {}).get('Start', [])
            body = payload[0]['data']['main'][0][0]['json']['body'] if payload else {}
            return EngineExecution(str(value['id']), str(value['workflowId']),
                str((value.get('workflowData') or {}).get('versionId') or value.get('workflowVersionId') or ''),
                value['status'], str(value['retryOf']) if value.get('retryOf') else None,
                str(value['retrySuccessId']) if value.get('retrySuccessId') else None,
                body.get('process_run_id'), body.get('invocation_token'))
        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Engine execution data is incomplete') from error

    def _versioned_execution(self, execution_id, visited):
        if execution_id in visited or len(visited) >= 20:
            raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Original execution version cannot be resolved safely')
        visited.add(execution_id)
        value = self.request('GET', '/api/v1/executions/' + quote(execution_id, safe='') + '?includeData=true')
        execution = self._execution(value)
        if execution.execution_id != execution_id:
            raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Engine execution identity is inconsistent')
        if execution.version_id:
            return execution, value
        # Native retries in 2.43.5 retain workflowData but omit workflowVersionId.
        # Inherit provenance only after comparing the saved executable snapshots.
        if not execution.retry_of:
            raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Execution has no saved version or original execution')
        parent, original = self._versioned_execution(execution.retry_of, visited)
        snapshot, previous = value.get('workflowData') or {}, original.get('workflowData') or {}
        if not snapshot.get('nodes') or not previous.get('nodes'):
            raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Original workflow snapshot is unavailable')
        if (execution.workflow_id != parent.workflow_id or execution.process_run_id != parent.process_run_id
                or execution.invocation_token != parent.invocation_token
                or any(snapshot.get(key) != previous.get(key) for key in ('nodes', 'connections', 'settings'))):
            raise CoreError('WORKFLOW_MISMATCH', 'Retry does not preserve the original executable workflow and inputs')
        return replace(execution, version_id=parent.version_id), value

    def execution(self, execution_id):
        return self._versioned_execution(execution_id, set())[0]

    def retry(self, execution_id):
        result = self._execution(self.request('POST', '/api/v1/executions/' + quote(execution_id, safe='') + '/retry',
                                              {'loadWorkflow': False}))
        return result if result.version_id else self.execution(result.execution_id)

    def find(self, workflow_id, process_id, retry_of=None):
        found = {}
        # The public list excludes running executions unless that status is requested.
        for status in (None, 'running'):
            cursor, visited = None, set()
            for _ in range(20):
                query = {'workflowId': workflow_id, 'includeData': 'true', 'limit': 100}
                if status:
                    query['status'] = status
                if cursor:
                    query['cursor'] = cursor
                page = self.request('GET', '/api/v1/executions?' + urlencode(query))
                if not isinstance(page.get('data'), list):
                    raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Engine execution history is incomplete')
                for row in page['data']:
                    execution = self._execution(row)
                    if execution.process_run_id == process_id and execution.retry_of == retry_of:
                        found[execution.execution_id] = execution if execution.version_id else self.execution(execution.execution_id)
                cursor = page.get('nextCursor')
                if not cursor:
                    break
                if cursor in visited:
                    raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Engine execution history cannot be fully reconciled')
                visited.add(cursor)
            else:
                raise CoreError('ENGINE_RESPONSE_UNKNOWN', 'Engine execution history exceeds the reconciliation limit')
        return list(found.values())
