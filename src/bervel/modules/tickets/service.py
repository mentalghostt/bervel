"""Tickets are saved drafts; sending is deliberately a separate, unavailable operation."""
import re
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.core.services import authorize, validate_key


def safe_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {'http', 'https'} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def contains_secret(value: str) -> bool:
    # A diagnostic may quote an already redacted configuration value. Only the
    # exact placeholder is exempt; a credential appended to it remains invalid.
    value = re.sub(r'(?i)(?:authorization\s*:\s*(?:bearer\s+)?|bearer\s+|(?:api[_ -]?key|password|secret|token|пароль|ключ\s*api)\s*[:=]\s*)\[СКРЫТО\](?=$|\s|["\'.,;)}\]]+(?:\s|$))', '', value)
    return bool(re.search(r'(?i)(?:authorization\s*:|bearer\s+\S+|(?:api[_ -]?key|password|secret|token|пароль|ключ\s*api)\s*[:=]\s*\S+|\bsk-[a-zA-Z0-9_-]{8,})', value))


def validate_content(value: dict) -> dict:
    fields = {'title': 200, 'problem': 8000, 'expected': 8000, 'actual': 8000, 'context': 8000}
    if not isinstance(value, dict) or set(value) != set(fields) | {'steps', 'links'}:
        raise CoreError('VALIDATION_ERROR', 'Заполните поля обращения; изменение статуса недоступно.')
    result = {}
    for name, maximum in fields.items():
        item = value[name]
        if not isinstance(item, str) or len(item) > maximum or (name in {'title', 'problem'} and not item.strip()):
            raise CoreError('VALIDATION_ERROR', 'Проверьте заголовок, проблему и длину полей обращения.')
        result[name] = item.strip()
    steps, links = value['steps'], value['links']
    if not isinstance(steps, list) or len(steps) > 50 or any(not isinstance(s, str) or not 1 <= len(s.strip()) <= 4000 for s in steps):
        raise CoreError('VALIDATION_ERROR', 'Укажите до50шагов длиной до4000символов.')
    if not isinstance(links, list) or len(links) > 30 or any(not isinstance(s, str) or len(s) > 2048 or not safe_url(s) for s in links):
        raise CoreError('VALIDATION_ERROR', 'Допустимы до30ссылок HTTP/HTTPS без учётных данных.')
    result.update(steps=[s.strip() for s in steps], links=links)
    if any(contains_secret(s) for s in [*result.values()] if isinstance(s, str)) or any(contains_secret(s) for s in steps + links):
        raise CoreError('VALIDATION_ERROR', 'Удалите ключи, пароли и заголовки авторизации из обращения.')
    return result


class TicketRepository(Protocol):
    def create(self, actor: ActorContext, content: dict, key: str) -> dict: ...
    def read(self, actor: ActorContext, ticket_id: UUID) -> dict: ...
    def list(self, actor: ActorContext, cursor: int, limit: int) -> dict: ...
    def edit(self, actor: ActorContext, ticket_id: UUID, content: dict, expected_revision: int, key: str) -> dict: ...


class TicketService:
    def __init__(self, repository: TicketRepository):
        self.repository = repository

    def create(self, actor: ActorContext, content: dict, key: str) -> dict:
        authorize(actor, 'chat:write'); validate_key(key)
        return self.repository.create(actor, validate_content(content), key)

    def read(self, actor: ActorContext, ticket_id: UUID) -> dict:
        authorize(actor, 'chat:read')
        return self.repository.read(actor, ticket_id)

    def list(self, actor: ActorContext, cursor: int = 0, limit: int = 20) -> dict:
        authorize(actor, 'chat:read')
        if type(cursor) is not int or type(limit) is not int or cursor < 0 or not 1 <= limit <= 100:
            raise CoreError('VALIDATION_ERROR', 'Неверные параметры списка обращений.')
        return self.repository.list(actor, cursor, limit)

    def edit(self, actor: ActorContext, ticket_id: UUID, content: dict, expected_revision: int, key: str) -> dict:
        authorize(actor, 'chat:write'); validate_key(key)
        if type(expected_revision) is not int or expected_revision < 1:
            raise CoreError('VALIDATION_ERROR', 'Укажите текущую редакцию обращения.')
        return self.repository.edit(actor, ticket_id, validate_content(content), expected_revision, key)
