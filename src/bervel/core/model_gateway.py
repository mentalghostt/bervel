"""Model contract and safe public failures, shared by consuming modules."""
from typing import NotRequired, Protocol, TypedDict


class ModelResult(TypedDict):
    answer: str
    model: str
    sources: NotRequired[list[dict[str, str]]]


MODEL_FAILURE_MESSAGES = {
    'MODEL_UNAVAILABLE': 'Соединение с исполнителем Codex прервалось или исполнитель недоступен. Выполненные действия сохранены; автоматического повтора нет.',
    'MODEL_UNCONFIGURED': 'Подключение к модели не настроено. Администратору нужно указать модель, адрес API и внешний источник авторизации.',
    'MODEL_TLS_ERROR': 'Не удалось проверить сертификат модельного API. Проверка остановлена; настройки сертификатов не изменялись.',
    'MODEL_AUTH_ERROR': 'Модельный API отклонил авторизацию. Проверьте доступ на стороне провайдера.',
    'MODEL_TIMEOUT': 'Время ожидания модели истекло. Исход запроса неизвестен; автоматический повтор отключён.',
    'MODEL_BUSY': 'Исполнитель Codex занят другим запросом. Отправьте новое сообщение после его завершения.',
    'MODEL_RATE_LIMITED': 'Провайдер Codex ограничил частоту запросов (429). Лимит ожидания исчерпан; выполненные действия сохранены и не будут повторены. Повторите вопрос позже.',
    'MODEL_FAILED': 'Codex не смог завершить запрос. Предыдущая команда не будет повторена автоматически.',
    'WEB_SEARCH_UNAVAILABLE': 'Штатный веб-поиск Codex недоступен через текущее подключение. Проверьте поддержку поиска на стороне шлюза Codex.',
    'WEB_SEARCH_FAILED': 'Не удалось получить проверяемые результаты веб-поиска.',
    'WEB_SEARCH_UNCONFIGURED': 'Подключение штатного веб-поиска Codex не настроено.',
    'WEB_SEARCH_AUTH_ERROR': 'Подключение Codex отклонило авторизацию веб-поиска. Проверьте доступ на стороне шлюза Codex.',
    'WEB_SEARCH_BUSY': 'Провайдер веб-поиска занят или ограничил частоту запросов.',
    'WEB_SEARCH_TIMEOUT': 'Время ожидания веб-поиска истекло; автоматический повтор отключён.',
    'MODEL_INVALID_OUTPUT': 'Модель вернула ответ, который не соответствует запрошенной структуре.',
}


class ModelClient(Protocol):
    def generate(self, request_id: str, prompt: str, *, images: list[dict] | None = None,
                 output_schema: dict | None = None, web_search: bool = False,
                 timeout_seconds: float | None = None) -> ModelResult: ...


