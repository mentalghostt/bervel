from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import secrets

from bervel.core.models import ActorContext

PERMISSIONS = frozenset({'module:read', 'artifact:read', 'artifact:write', 'dataset:read', 'dataset:publish'})


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    object_root: Path
    access_keys: dict[str, ActorContext] = field(repr=False)
    host: str = '127.0.0.1'
    port: int = 8011

    def __post_init__(self):
        if not ipaddress.ip_address(self.host).is_loopback or not 1 <= self.port <= 65535:
            raise ValueError('Educational API requires a loopback address and valid port')
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
                       os.environ.get('BERVEL_HOST', '127.0.0.1'), int(os.environ.get('BERVEL_PORT', '8011')))
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError('Invalid BERVEL environment configuration; check database, object root and access bindings') from error
