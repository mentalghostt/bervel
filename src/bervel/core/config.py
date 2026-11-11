from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from bervel.core.models import ActorContext

PERMISSIONS = frozenset({'module:read', 'artifact:read', 'artifact:write', 'dataset:read', 'dataset:publish', 'chat:read', 'chat:write', 'settings:read', 'settings:write',
                         'process:start', 'process:read', 'process:recover'})


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    object_root: Path
    access_keys: dict[str, ActorContext] = field(repr=False)
    host: str = '127.0.0.1'
    port: int = 8011
    codex_url: str = field(default='http://codex:8090', repr=False)
    embedding_api_key: str = field(default='',repr=False)
    embedding_provider: str = 'local'
    embedding_cache: Path = Path('/data/embedding-models')
    inspection_url: str = 'http://inspection-reader:8091'
    inspection_token: str = field(default='', repr=False)
    kontur_api_key: str = field(default='', repr=False)
    engineering_url: str = 'http://inspection-executor:8092'
    internal_api_url: str = 'http://api:8011'
    ai_radar_kontur_base_url: str = 'https://api-zakupki.kontur.ru'

    def __post_init__(self):
        inspector = urlsplit(self.inspection_url)
        if inspector.scheme not in {'http', 'https'} or not inspector.hostname or inspector.username or inspector.password or inspector.query or inspector.fragment:
            raise ValueError('Invalid inspection reader URL')
        for address in (self.engineering_url, self.internal_api_url):
            parsed = urlsplit(address)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
                raise ValueError('Invalid inspection engineering URL')
        if self.embedding_provider not in ('local','openai') or not self.embedding_cache.is_absolute():
            raise ValueError('Invalid embedding provider/cache')
        runner = urlsplit(self.codex_url)
        if runner.scheme not in {'http', 'https'} or not runner.hostname or runner.username or runner.password or runner.query or runner.fragment:
            raise ValueError('Invalid internal Codex runner URL')
        if (not ipaddress.ip_address(self.host).is_loopback and self.host != '0.0.0.0') or not 1 <= self.port <= 65535:
            raise ValueError('API requires a loopback or explicit IPv4 wildcard address and valid port')
        if not self.object_root.is_absolute() or not self.access_keys:
            raise ValueError('Absolute object root and access keys are required')
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

    @classmethod
    def from_environment(cls) -> 'Settings':
        try:
            bindings = json.loads(os.environ['BERVEL_ACCESS_KEYS'])
            keys = {}
            for item in bindings:
                if item['key'] in keys:
                    raise ValueError('Duplicate key binding')
                keys[item['key']] = ActorContext(item['actor_id'], item['data_scope'], frozenset(item['permissions']))
            return cls(os.environ['BERVEL_DATABASE_URL'], Path(os.environ['BERVEL_OBJECT_ROOT']), keys,
                       os.environ.get('BERVEL_HOST', '127.0.0.1'), int(os.environ.get('BERVEL_PORT', '8011')),
                       os.environ.get('BERVEL_CODEX_URL', 'http://codex:8090'),
                       os.environ.get('BERVEL_EMBEDDING_API_KEY',''),os.environ.get('BERVEL_EMBEDDING_PROVIDER','local'),
                       Path(os.environ.get('BERVEL_EMBEDDING_CACHE','/data/embedding-models')),
                       os.environ.get('BERVEL_INSPECTION_URL', 'http://inspection-reader:8091'),
                       os.environ.get('BERVEL_INSPECTION_TOKEN', ''),os.environ.get('KONTUR_API_KEY',''),
                       os.environ.get('BERVEL_ENGINEERING_URL', 'http://inspection-executor:8092'),
                       os.environ.get('BERVEL_INTERNAL_API_URL', 'http://api:8011'),
                       os.environ.get('BERVEL_AI_RADAR_KONTUR_BASE_URL', 'https://api-zakupki.kontur.ru'))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('Invalid BERVEL environment configuration; check database, object root and access bindings') from error
