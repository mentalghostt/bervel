"""Create local Docker secrets once, without printing or replacing existing values."""
import json
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[2]
target = root / '.env'
if target.exists():
    print('Existing .env preserved.')
else:
    values = {name: secrets.token_hex(32) for name in (
        'POSTGRES_PASSWORD', 'BERVEL_DB_PASSWORD', 'BERVEL_TEST_DB_PASSWORD',
        'N8N_DB_PASSWORD', 'N8N_ENCRYPTION_KEY', 'REDIS_PASSWORD', 'BERVEL_E2E_ACCESS_KEY')}
    historical_key = root / '.dev/local/core-stage-2/encryption.env'
    if historical_key.exists():
        name, separator, value = historical_key.read_text().strip().partition('=')
        if name != 'N8N_ENCRYPTION_KEY' or not separator or not value:
            raise ValueError('Historical n8n encryption key file is invalid; do not replace it.')
        values['N8N_ENCRYPTION_KEY'] = value
    permissions = ['module:read', 'artifact:read', 'artifact:write', 'dataset:read', 'dataset:publish']
    bindings = [{'key': secrets.token_hex(32), 'actor_id': 'local-user-' + letter,
                 'data_scope': 'test-area-' + letter, 'permissions': permissions} for letter in ('a', 'b')]
    text = '# Local Docker configuration. Contains secrets; keep outside Git.\nCOMPOSE_PROJECT_NAME=bervel\n'
    text += ''.join(f'{name}={value}\n' for name, value in values.items())
    text += "BERVEL_ACCESS_KEYS='" + json.dumps(bindings, separators=(',', ':')) + "'\n"
    target.write_text(text, encoding='utf-8')
    target.chmod(0o600)
    print('Created .env with generated secrets; existing n8n encryption key preserved when present.')
