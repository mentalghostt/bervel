"""Internal runner HTTP adapter. No shell, credentials or executable user input."""
import json
import math
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bervel.core.errors import CoreError
from bervel.core.model_gateway import MODEL_FAILURE_MESSAGES


class CodexClient:
    def __init__(self, url: str):
        self.url = url.rstrip('/')

    def generate(self, request_id: str, prompt: str, *, images: list[dict] | None = None,
                 output_schema: dict | None = None, web_search: bool = False,
                 timeout_seconds: float | None = None) -> dict:
        payload = {'request_id': request_id, 'prompt': prompt,
            **({'images': images} if images else {}), **({'output_schema': output_schema} if output_schema is not None else {}),
            **({'web_search': True} if web_search else {})}
        transport_timeout = 190
        if timeout_seconds is not None:
            if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                    or not math.isfinite(timeout_seconds)):
                raise CoreError('VALIDATION_ERROR', 'Model time budget must be a finite number')
            timeout_ms = int(max(1, min(180, timeout_seconds)) * 1000)
            payload['timeout_ms'] = timeout_ms
            transport_timeout = min(190, timeout_ms / 1000 + 2)
        result = self._request('POST', '/v1/generate', payload, timeout_seconds=transport_timeout)
        if (not isinstance(result.get('answer'), str) or not result['answer'].strip() or len(result['answer']) > 32000
                or not isinstance(result.get('model'), str) or not 1 <= len(result['model']) <= 200):
            raise CoreError('MODEL_INVALID_OUTPUT', MODEL_FAILURE_MESSAGES['MODEL_INVALID_OUTPUT'])
        response = {'answer': result['answer'], 'model': result['model']}
        if web_search:
            sources = result.get('sources')
            if not isinstance(sources, list) or not sources or len(sources)>100 or any(not isinstance(s, dict)
                    or not isinstance(s.get('title'), str) or len(s['title'])>1000 or not isinstance(s.get('url'), str)
                    or len(s['url'])>2048 for s in sources):
                raise CoreError('WEB_SEARCH_FAILED', MODEL_FAILURE_MESSAGES['WEB_SEARCH_FAILED'])
            response['sources'] = [{'title': s['title'], 'url': s['url']} for s in sources]
        return response

    def settings_request(self, method: str, path: str, body: dict | None = None) -> dict:
        return self._request(method, path, body)

    def _request(self, method: str, path: str, payload: dict | None, *, timeout_seconds: float = 190) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else b''
        if len(body) > 8 * 1024 * 1024:
            raise CoreError('QUOTA_EXCEEDED', 'Model context exceeds the request limit')
        request = Request(self.url + path, data=body if payload is not None else None,
                          headers={'Content-Type': 'application/json'}, method=method)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                content = response.read(256 * 1024 + 1)
            if len(content) > 256 * 1024:
                raise ValueError('Response too large')
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError('Invalid model response')
            return result
        except HTTPError as error:
            detail={}
            try:
                payload = json.loads(error.read(8192))
                detail=payload.get('error', {}) if isinstance(payload, dict) else {}
                code = detail.get('code')
            except (ValueError, OSError, AttributeError):
                code = None
            finally:
                error.close()
            if isinstance(code, str) and code in MODEL_FAILURE_MESSAGES:
                failure=CoreError(code, MODEL_FAILURE_MESSAGES[code])
                delay=detail.get('retry_after_seconds')
                if (code in {'MODEL_RATE_LIMITED','MODEL_BUSY'} and type(delay) in (int,float)
                        and 0<=delay<=2**31-1):
                    failure.retry_after_seconds=delay
                raise failure from error
            if code == 'INVALID_SETTINGS':
                raise CoreError(code, 'Проверьте провайдер, модель, HTTPS-адрес и ключ доступа.') from error
            if code == 'SETTINGS_UNAVAILABLE':
                raise CoreError(code, 'Хранилище настроек недоступно.') from error
            raise CoreError('MODEL_UNAVAILABLE', MODEL_FAILURE_MESSAGES['MODEL_UNAVAILABLE']) from error
        except (URLError, OSError) as error:
            raise CoreError('MODEL_UNAVAILABLE', MODEL_FAILURE_MESSAGES['MODEL_UNAVAILABLE']) from error
        except ValueError as error:
            raise CoreError('MODEL_INVALID_OUTPUT', MODEL_FAILURE_MESSAGES['MODEL_INVALID_OUTPUT']) from error
