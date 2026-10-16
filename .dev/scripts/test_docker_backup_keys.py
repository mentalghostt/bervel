from pathlib import Path

import pytest


def key_file(directory: Path, name: str, value: str) -> Path:
    path = directory / name
    path.write_text('N8N_ENCRYPTION_KEY=' + value + '\n')
    return path


def test_matching_backup_encryption_key_allows_restore(tmp_path):
    from docker_backup_keys import validate_backup_key
    backup = key_file(tmp_path, 'backup.env', 'a' * 64)
    environment = key_file(tmp_path, 'current.env', 'a' * 64)
    validate_backup_key(backup, environment)


def test_wrong_encryption_key_refuses_restore_without_exposing_secrets(tmp_path):
    from docker_backup_keys import validate_backup_key
    original = 'a' * 64
    current = 'b' * 64
    backup = key_file(tmp_path, 'backup.env', original)
    environment = key_file(tmp_path, 'current.env', current)
    with pytest.raises(ValueError) as error:
        validate_backup_key(backup, environment)
    assert original not in str(error.value)
    assert current not in str(error.value)


def test_missing_backup_key_refuses_restore(tmp_path):
    from docker_backup_keys import validate_backup_key
    environment = key_file(tmp_path, 'current.env', 'a' * 64)
    with pytest.raises(ValueError):
        validate_backup_key(tmp_path / 'missing.env', environment)
