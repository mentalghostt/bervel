from pathlib import Path
import re
import os
import tempfile
from typing import BinaryIO

from bervel.core.errors import CoreError
from bervel.core.models import StoredObject
from bervel.core.originals import OriginalContent, content_chunks, content_digest


class FileObjectStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def put(self, content: OriginalContent) -> StoredObject:
        digest, size = content_digest(content)
        ref = 'sha256:' + digest
        temporary = None
        try:
            directory = self.root / digest[:2]
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / digest
            if target.exists():
                with self.open(ref) as existing:
                    if content_digest(existing) != (digest, size):
                        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Stored object failed integrity verification')
            else:
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as file:
                    temporary = Path(file.name)
                    for chunk in content_chunks(content):
                        file.write(chunk)
                    file.flush()
                    os.fsync(file.fileno())
                # Atomic hard link: only one writer can publish this immutable hash key.
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    pass  # The winner is verified below before returning the hash key.
            with self.open(ref) as verified:
                if content_digest(verified) != (digest, size):
                    raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Object write did not preserve bytes')
            return StoredObject(ref, digest, size)
        except OSError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Cannot save original object') from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def read(self, object_ref: str) -> bytes:
        try:
            with self.open(object_ref) as stream:
                return stream.read()
        except OSError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Cannot read original object') from error

    def open(self, object_ref: str) -> BinaryIO:
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', object_ref):
            raise CoreError('VALIDATION_ERROR', 'Invalid logical object reference')
        digest = object_ref.split(':', 1)[1]
        try:
            stream = (self.root / digest[:2] / digest).open('rb')
        except OSError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Cannot read original object') from error
        try:
            if content_digest(stream)[0] != digest:
                raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Stored object failed integrity verification')
            return stream
        except BaseException:
            stream.close()
            raise

    def delete(self, object_ref: str) -> None:
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', object_ref):
            raise CoreError('VALIDATION_ERROR', 'Invalid logical object reference')
        digest = object_ref.split(':', 1)[1]
        target = self.root / digest[:2] / digest
        try:
            if not target.parent.resolve().is_relative_to(self.root):
                raise CoreError('VALIDATION_ERROR', 'Object is outside the storage root')
            target.unlink(missing_ok=True)
        except OSError as error:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Cannot remove original object') from error
