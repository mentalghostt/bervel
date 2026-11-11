import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.source_contract import SIMULATOR_KEY


class KonturCredentials:
    """The API writes this private file atomically; the worker shares it read-only."""

    def __init__(self, object_root: Path, environment_key: str):
        self.path = object_root / '.connection-secrets' / 'kontur-api-key'
        self.environment_key = environment_key if environment_key != SIMULATOR_KEY else ''

    def read(self) -> str:
        try:
            return self.path.read_text(encoding='ascii')
        except FileNotFoundError:
            return self.environment_key
        except (OSError, UnicodeError):
            raise CoreError('SETTINGS_UNAVAILABLE', 'Хранилище ключа Контур.Закупок недоступно.') from None

    def save(self, key: str) -> None:
        temporary = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=self.path.parent, mode='w', encoding='ascii', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(key)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except OSError:
            raise CoreError('SETTINGS_UNAVAILABLE', 'Не удалось сохранить ключ Контур.Закупок.') from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
