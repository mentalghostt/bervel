"""Create local Docker secrets once, without printing or replacing existing values."""
import json
import os
from pathlib import Path
import re
import secrets

PERMISSIONS = ['module:read', 'artifact:read', 'artifact:write', 'dataset:read', 'dataset:publish']
CHAT_PERMISSIONS = ['chat:read', 'chat:write']
SETTINGS_PERMISSIONS = ['settings:read', 'settings:write']


def initialize_env(root: Path) -> None:
    target = root / '.env'
    if target.exists():
        text = target.read_text(encoding='utf-8')
        settings = {}
        for line in text.splitlines():
            name, separator, value = line.partition('=')
            if separator and not name.lstrip().startswith('#'):
                settings[name.strip()] = value.strip().strip("'\"")
        try:
            bindings = json.loads(settings['BERVEL_ACCESS_KEYS'])
        except (KeyError, ValueError):
            raise ValueError('Existing BERVEL_ACCESS_KEYS must be valid JSON; configuration unchanged.') from None
        candidates = [binding['key'] for binding in bindings
                      if set(PERMISSIONS) <= set(binding.get('permissions', []))
                      and re.fullmatch(r'[A-Za-z0-9_-]{32,}', binding.get('key', ''))]
        configured = settings.get('BERVEL_WEB_ACCESS_KEY')
        if configured:
            if configured not in candidates:
                raise ValueError('BERVEL_WEB_ACCESS_KEY must match an API binding with web permissions; configuration unchanged.')
            added_inspection = False
            if not settings.get('BERVEL_ENGINEERING_HOST_PROJECT_ROOT'):
                text += ('\n' if text and not text.endswith('\n') else '') + 'BERVEL_ENGINEERING_HOST_PROJECT_ROOT=' + os.environ.get('BERVEL_ENGINEERING_HOST_PROJECT_ROOT', root.resolve().as_posix()) + '\n'
                added_inspection = True
            for name in ('BERVEL_INSPECTION_DB_PASSWORD', 'BERVEL_INSPECTION_TOKEN'):
                if not settings.get(name):
                    text += ('\n' if text and not text.endswith('\n') else '') + name + '=' + secrets.token_hex(32) + '\n'
                    added_inspection = True
            selected = next(binding for binding in bindings if binding['key'] == configured)
            missing = [permission for permission in CHAT_PERMISSIONS + SETTINGS_PERMISSIONS if permission not in selected['permissions']]
            if missing:
                selected['permissions'] = selected['permissions'] + missing
                replacement = "BERVEL_ACCESS_KEYS='" + json.dumps(bindings, separators=(',', ':')) + "'"
                text = re.sub(r'^BERVEL_ACCESS_KEYS=.*$', lambda _: replacement, text, flags=re.MULTILINE)
                target.write_text(text, encoding='utf-8')
                print('Added chat and model settings permissions to the selected web binding; existing secrets preserved.')
                return
            if added_inspection:
                target.write_text(text, encoding='utf-8')
            print('Existing application secrets preserved; web and inspection access are configured.')
            return
        if not candidates:
            raise ValueError('No API binding has the required web permissions and token format; configuration unchanged.')
        text += ('\n' if text and not text.endswith('\n') else '') + 'BERVEL_WEB_ACCESS_KEY=' + candidates[0] + '\n'
        target.write_text(text, encoding='utf-8')
        initialize_env(root)
        return
    values = {name: secrets.token_hex(32) for name in (
        'POSTGRES_PASSWORD', 'BERVEL_DB_PASSWORD', 'BERVEL_TEST_DB_PASSWORD',
        'BERVEL_E2E_ACCESS_KEY', 'BERVEL_INSPECTION_DB_PASSWORD', 'BERVEL_INSPECTION_TOKEN')}
    bindings = [{'key': secrets.token_hex(32), 'actor_id': 'local-user-' + letter,
                 'data_scope': 'test-area-' + letter, 'permissions': PERMISSIONS + CHAT_PERMISSIONS + ['process:start', 'process:read', 'process:recover']
                 + (SETTINGS_PERMISSIONS if letter == 'a' else [])} for letter in ('a', 'b')]
    text = '# Local Docker configuration. Contains secrets; keep outside Git.\nCOMPOSE_PROJECT_NAME=bervel\n'
    text += ''.join(f'{name}={value}\n' for name, value in values.items())
    text += "BERVEL_ACCESS_KEYS='" + json.dumps(bindings, separators=(',', ':')) + "'\n"
    text += 'BERVEL_WEB_ACCESS_KEY=' + bindings[0]['key'] + '\n'
    text += 'BERVEL_ENGINEERING_HOST_PROJECT_ROOT=' + os.environ.get('BERVEL_ENGINEERING_HOST_PROJECT_ROOT', root.resolve().as_posix()) + '\n'
    target.write_text(text, encoding='utf-8')
    target.chmod(0o600)
    print('Created .env with generated application secrets.')


if __name__ == '__main__':
    initialize_env(Path(__file__).resolve().parents[2])
