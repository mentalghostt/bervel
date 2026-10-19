from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import secrets

from bervel.core.models import ActorContext

PERMISSIONS = frozenset({'module:read', 'artifact:read', 'artifact:write', 'dataset:read', 'dataset:publish',
                         'process:start', 'process:read', 'process:recover'})


@dataclass(frozen=True)
class N8nSettings:
    base_url: str
    api_key: str = field(repr=False)
    webhook_key: str = field(repr=False)
    workflow_id: str = ''
    webhook_path: str = 'bervel-document-review-v1'
    callback_url: str = 'http://api:8011'
    timeout: int = 15

    def __post_init__(self):
        from urllib.parse import urlsplit
        for url in (self.base_url, self.callback_url):
            parsed = urlsplit(url)
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or any(character in url for character in "\r\n'\"{}")):
                raise ValueError('Invalid orchestration URL')
        for key in (self.api_key, self.webhook_key):
            if len(key) < 32 or not key.isascii() or any(character in key for character in '\r\n'):
                raise ValueError('Invalid orchestration key')
        if not 1 <= self.timeout <= 120:
            raise ValueError('Invalid orchestration timeout')


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    object_root: Path
    access_keys: dict[str, ActorContext] = field(repr=False)
    host: str = '127.0.0.1'
    port: int = 8011
    process_worker_key: str = field(default='', repr=False)
    n8n: N8nSettings | None = field(default=None, repr=False)

    def __post_init__(self):
        if (not ipaddress.ip_address(self.host).is_loopback and self.host != '0.0.0.0') or not 1 <= self.port <= 65535:
            raise ValueError('API requires a loopback or explicit IPv4 wildcard address and valid port')
        if not self.object_root.is_absolute() or not self.access_keys:
            raise ValueError('Absolute object root and access keys are required')
        if self.process_worker_key and (len(self.process_worker_key) < 32 or not self.process_worker_key.isascii()
                or self.process_worker_key in self.access_keys or any(c in self.process_worker_key for c in '\r\n')):
            raise ValueError('Worker key must be a separate random secret')
        if self.n8n is not None and not self.process_worker_key:
            raise ValueError('Orchestration requires a worker key')
        for key, actor in self.access_keys.items():
            if not key.isascii() or len(key) < 32 or not 1 <= len(actor.actor_id) <= 128 or not 1 <= len(actor.data_scope) <= 128:
                raise ValueError('Invalid access-key binding')
            if not actor.permissions <= PERMISSIONS:
                raise ValueError('Unrecognized permission')

    def authenticate(self, token: str) -> ActorContext | None:
        for key, context in self.access_keys.items():
            if secrets.compare_digest(key.encode('utf-8'), token.encode('utf-8')):
                return context
        return None

    def authenticate_worker(self, token: str) -> bool:
        return bool(self.process_worker_key) and secrets.compare_digest(self.process_worker_key.encode(), token.encode())

    @classmethod
    def from_environment(cls) -> 'Settings':
        try:
            bindings = json.loads(os.environ['BERVEL_ACCESS_KEYS'])
            keys = {}
            for item in bindings:
                if item['key'] in keys:
                    raise ValueError('Duplicate key binding')
                keys[item['key']] = ActorContext(item['actor_id'], item['data_scope'], frozenset(item['permissions']))
            n8n = N8nSettings(os.environ['BERVEL_N8N_URL'], os.environ['BERVEL_N8N_API_KEY'],
                os.environ['BERVEL_N8N_WEBHOOK_KEY'], os.environ.get('BERVEL_N8N_WORKFLOW_ID', ''),
                os.environ.get('BERVEL_N8N_WEBHOOK_PATH', 'bervel-document-review-v1'),
                os.environ.get('BERVEL_N8N_CALLBACK_URL', 'http://api:8011'),
                int(os.environ.get('BERVEL_N8N_TIMEOUT', '15'))) if os.environ.get('BERVEL_N8N_URL') else None
            return cls(os.environ['BERVEL_DATABASE_URL'], Path(os.environ['BERVEL_OBJECT_ROOT']), keys,
                       os.environ.get('BERVEL_HOST', '127.0.0.1'), int(os.environ.get('BERVEL_PORT', '8011')),
                       os.environ.get('BERVEL_PROCESS_WORKER_KEY', ''), n8n)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('Invalid BERVEL environment configuration; check database, object root and access bindings') from error
