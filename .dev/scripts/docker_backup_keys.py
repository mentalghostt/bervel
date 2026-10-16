"""Validate the encryption key required to restore n8n credentials."""
from pathlib import Path
import secrets


def read_key(path: Path) -> str:
    try:
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.startswith('N8N_ENCRYPTION_KEY='):
                value = line.partition('=')[2].strip().strip("'\"")
                if value:
                    return value
    except OSError:
        pass
    raise ValueError('A saved N8N_ENCRYPTION_KEY is required before restoring n8n.')


def validate_backup_key(backup: Path, environment: Path) -> None:
    if not secrets.compare_digest(read_key(backup), read_key(environment)):
        raise ValueError('Restore refused: set N8N_ENCRYPTION_KEY in .env from the saved backup key before restoring any database.')
