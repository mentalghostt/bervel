import hashlib
from pathlib import Path
import re
import os
import tempfile

from bervel.core.errors import CoreError
from bervel.core.models import StoredObject


class FileObjectStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def put(self, content: bytes) -> StoredObject:
        digest = hashlib.sha256(content).hexdigest()
        ref = 'sha256:' + digest
        temporary = None
        try:
            directory = self.root / digest[:2]
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / digest
            if target.exists():
                if self.read(ref) != content:
                    raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Stored object failed integrity verification')
            else:
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as file:
                    temporary = Path(file.name)
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
                # Atomic hard link: only one writer can publish this immutable hash key.
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    if self.read(ref) != content:
                        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Stored object failed integrity verification')
            if self.read(ref) != content:
                raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Object write did not preserve bytes')
            return StoredObject(ref, digest, len(content))
        except OSError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Cannot save original object') from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def read(self, object_ref: str) -> bytes:
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', object_ref):
            raise CoreError('VALIDATION_ERROR', 'Invalid logical object reference')
        digest = object_ref.split(':', 1)[1]
        try:
            content = (self.root / digest[:2] / digest).read_bytes()
        except OSError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Cannot read original object') from error
        if hashlib.sha256(content).hexdigest() != digest:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Stored object failed integrity verification')
        return content
