"""Private asynchronous native CLI job transport; never rewrites model input."""
import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

from bervel.core.errors import CoreError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise CoreError('INSPECTION_CLI_TRANSPORT', 'Перенаправление native CLI запрещено.')


class NativeCliClient:
    def __init__(self, base_url, token, api_key_for_actor, *, poll_interval=0.5):
        self.base_url, self.token, self.api_key_for_actor = base_url.rstrip('/'), token, api_key_for_actor
        self.poll_interval = poll_interval
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _request(self, actor, turn, method, path, body=None):
        request = Request(self.base_url + path, data=json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                value = json.load(response)
        except HTTPError as error:
            try: detail = json.loads(error.read(65536)).get('error', {}).get('code')
            except (OSError, HTTPException, ValueError, AttributeError): detail = None
            finally: error.close()
            if error.code == 404 and detail == 'NATIVE_JOB_NOT_FOUND':
                code = 'INSPECTION_CLI_NOT_FOUND'
            elif path == '/v1/inspection/jobs' and error.code in {400, 401, 403, 409, 413, 422}:
                code = 'INSPECTION_CLI_REJECTED'
            else:
                code = 'INSPECTION_CLI_TRANSPORT'
            raise CoreError(code, 'Состояние native CLI недоступно. Запрос автоматически не запускается повторно.') from None
        except (OSError, URLError, HTTPException, ValueError):
            raise CoreError('INSPECTION_CLI_TRANSPORT', 'Связь с native CLI прервана; исполнение может продолжаться.') from None
        identity = {'request_id': str(turn['turn_id']), 'session_id': str(turn['session_id']),
            'actor_id': actor.actor_id, 'data_scope': actor.data_scope}
        if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in identity.items()):
            raise CoreError('INSPECTION_CLI_TRANSPORT', 'Native CLI вернул чужой запрос или диалог; состояние текущего запроса не подтверждено.')
        if value.get('state') not in {'queued', 'running', 'completed', 'failed', 'cancelled', 'unknown'}:
            raise CoreError('INSPECTION_CLI_TRANSPORT', 'Некорректное состояние native CLI.')
        return value

    def start(self, actor, turn):
        key = self.api_key_for_actor(actor)
        if not key: raise CoreError('FORBIDDEN', 'Нет действующей авторизации пользователя.')
        return self._request(actor, turn, 'POST', '/v1/inspection/jobs',
            {'request_id': str(turn['turn_id']), 'session_id': str(turn['session_id']),
                'actor_id': actor.actor_id, 'data_scope': actor.data_scope, 'question': turn['question'], 'api_key': key})

    def read(self, actor, turn):
        return self._request(actor, turn, 'GET', '/v1/inspection/jobs/' + str(turn['turn_id']))

    def cancel(self, actor, turn):
        return self._request(actor, turn, 'POST', '/v1/inspection/jobs/' + str(turn['turn_id']) + '/cancel', {})
