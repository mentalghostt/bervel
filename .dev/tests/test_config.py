from pathlib import Path

import pytest

from bervel.config import Settings
from bervel.core.models import ActorContext


def configured(tmp_path: Path, **overrides) -> Settings:
    return Settings('postgresql+psycopg://app@postgres/app', tmp_path,
                    {'a' * 32: ActorContext('actor', 'area', frozenset({'artifact:read'}))}, **overrides)


def test_container_can_explicitly_bind_all_ipv4_interfaces(tmp_path):
    assert configured(tmp_path, host='0.0.0.0').host == '0.0.0.0'


def test_local_launch_still_binds_loopback_by_default(tmp_path):
    assert configured(tmp_path).host == '127.0.0.1'


@pytest.mark.parametrize('host,port', [('api', 8011), ('192.0.2.1', 8011),
                                      ('127.0.0.1', 0), ('0.0.0.0', 65536)])
def test_invalid_or_unintended_bind_configuration_is_rejected(tmp_path, host, port):
    with pytest.raises(ValueError):
        configured(tmp_path, host=host, port=port)


def test_worker_key_is_optional_separate_and_hidden(tmp_path):
    settings = configured(tmp_path, process_worker_key='w' * 32)
    assert settings.authenticate('w' * 32) is None
    assert settings.authenticate_worker('w' * 32)
    assert not configured(tmp_path).authenticate_worker('')
    assert 'w' * 32 not in repr(settings)
    with pytest.raises(ValueError):
        configured(tmp_path, process_worker_key='a' * 32)


def test_process_permissions_and_invalid_partial_n8n_environment(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('BERVEL_DATABASE_URL', 'postgresql+psycopg://app@postgres/app')
    monkeypatch.setenv('BERVEL_OBJECT_ROOT', str(tmp_path))
    monkeypatch.setenv('BERVEL_ACCESS_KEYS', json.dumps([{'key': 'a' * 32, 'actor_id': 'operator', 'data_scope': 'area',
        'permissions': ['process:start', 'process:read', 'process:recover']}]))
    assert Settings.from_environment().access_keys['a' * 32].permissions == {'process:start', 'process:read', 'process:recover'}
    monkeypatch.setenv('BERVEL_N8N_URL', 'http://n8n-main:5688')
    with pytest.raises(ValueError):
        Settings.from_environment()
