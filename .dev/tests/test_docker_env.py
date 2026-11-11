import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('docker_env', Path(__file__).parents[1] / 'scripts/docker_env.py')
docker_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docker_env)


def test_fresh_configuration_selects_an_existing_web_binding(tmp_path, capsys):
    docker_env.initialize_env(tmp_path)
    text = (tmp_path / '.env').read_text()
    bindings = json.loads(next(line.split('=', 1)[1].strip("'") for line in text.splitlines()
                              if line.startswith('BERVEL_ACCESS_KEYS=')))
    assert 'BERVEL_WEB_ACCESS_KEY=' + bindings[0]['key'] in text
    assert {'settings:read', 'settings:write'} <= set(bindings[0]['permissions'])
    assert not {'settings:read', 'settings:write'} & set(bindings[1]['permissions'])
    assert bindings[0]['key'] not in capsys.readouterr().out
    original = (tmp_path / '.env').read_bytes()
    docker_env.initialize_env(tmp_path)
    assert (tmp_path / '.env').read_bytes() == original


def test_existing_configuration_adds_web_setting_and_keeps_selected_key(tmp_path, capsys):
    bindings = [{'key': 'a' * 64, 'permissions': ['artifact:read']},
                {'key': 'b' * 64, 'permissions': docker_env.PERMISSIONS}]
    original = "# Keep existing secrets\nPOSTGRES_PASSWORD=keep-this\nBERVEL_ACCESS_KEYS='" + json.dumps(bindings) + "'\n"
    (tmp_path / '.env').write_text(original)
    docker_env.initialize_env(tmp_path)
    updated = (tmp_path / '.env').read_text()
    assert 'BERVEL_WEB_ACCESS_KEY=' + 'b' * 64 + '\n' in updated
    assert 'POSTGRES_PASSWORD=keep-this' in updated
    assert 'chat:read' in updated and 'chat:write' in updated
    assert 'b' * 64 not in capsys.readouterr().out
    # Re-running initialization must not replace an explicitly selected binding.
    docker_env.initialize_env(tmp_path)
    assert (tmp_path / '.env').read_text() == updated


def test_incompatible_configuration_is_preserved(tmp_path):
    original = "BERVEL_ACCESS_KEYS='[]'\n"
    (tmp_path / '.env').write_text(original)
    with pytest.raises(ValueError, match='No API binding'):
        docker_env.initialize_env(tmp_path)
    assert (tmp_path / '.env').read_text() == original


def test_only_selected_full_web_binding_receives_chat_permissions_without_secret_rotation(tmp_path, capsys):
    bindings = [{'key': 'a' * 64, 'actor_id': 'reader', 'permissions': ['artifact:read']},
                {'key': 'b' * 64, 'actor_id': 'web', 'permissions': docker_env.PERMISSIONS},
                {'key': 'c' * 64, 'actor_id': 'other', 'permissions': docker_env.PERMISSIONS}]
    original = "POSTGRES_PASSWORD=keep-this\nBERVEL_ACCESS_KEYS='" + json.dumps(bindings) + "'\nBERVEL_WEB_ACCESS_KEY=" + 'b' * 64 + '\n'
    (tmp_path / '.env').write_text(original)
    docker_env.initialize_env(tmp_path)
    updated = (tmp_path / '.env').read_text()
    actual = json.loads(next(line.split('=', 1)[1].strip("'") for line in updated.splitlines()
                             if line.startswith('BERVEL_ACCESS_KEYS=')))
    assert actual[0] == bindings[0] and actual[2] == bindings[2]
    assert set(actual[1]['permissions']) == set(bindings[1]['permissions']) | {'chat:read', 'chat:write', 'settings:read', 'settings:write'}
    assert actual[1]['key'] == bindings[1]['key'] and 'POSTGRES_PASSWORD=keep-this' in updated
    assert 'b' * 64 not in capsys.readouterr().out
    docker_env.initialize_env(tmp_path)
    assert (tmp_path / '.env').read_text() == updated
