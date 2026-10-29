"""A fixed internal HTTP destination, never a model-selected URL."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bervel.core.errors import CoreError


class InspectionClient:
    def __init__(self, base_url: str, token: str):
        self.base_url, self.token = base_url.rstrip('/'), token

    def _request(self, path: str, body: dict):
        request = Request(self.base_url + path, data=json.dumps(body, ensure_ascii=False).encode(),
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=65) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024: raise CoreError('QUOTA_EXCEEDED', 'Ответ проверки слишком велик; сузьте страницу чтения.')
                return json.loads(raw)
        except HTTPError as error:
            try: code = json.loads(error.read(4096)).get('error', {}).get('code', '')
            except ValueError: code = ''
            allowed = {'FORBIDDEN', 'NOT_FOUND', 'VALIDATION_ERROR', 'QUOTA_EXCEEDED', 'INSPECTION_UNAVAILABLE'}
            raise CoreError(code if code in allowed else 'INSPECTION_UNAVAILABLE', 'Диагностическое чтение не выполнено; уточните источник или проверьте сервис «Проверки».') from None
        except (OSError, URLError, ValueError) as error:
            raise CoreError('INSPECTION_UNAVAILABLE', 'Сервис «Проверки» недоступен. Изменения не выполнялись.') from error

    def read(self, operation: str, arguments: dict) -> dict:
        from bervel.modules.inspection.contracts import validate_operation
        validate_operation(operation, arguments, reads_only=True)
        return self._request('/read', {'operation': operation, 'arguments': arguments})

    def redact(self, value):
        return self._request('/redact', {'value': value})['value']
