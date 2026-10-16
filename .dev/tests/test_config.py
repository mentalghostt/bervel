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
