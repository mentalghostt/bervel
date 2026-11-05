"""Bounded access to bytes and seekable original-file streams."""
from hashlib import sha256
from typing import BinaryIO, Iterator
from bervel.core.errors import CoreError

OriginalContent = bytes | BinaryIO
CHUNK_BYTES = 1024 * 1024


def content_chunks(content: OriginalContent) -> Iterator[bytes]:
    if isinstance(content, bytes):
        for offset in range(0, len(content), CHUNK_BYTES):
            yield content[offset:offset + CHUNK_BYTES]
        return
    try:
        content.seek(0)
        try:
            while chunk := content.read(CHUNK_BYTES):
                yield chunk
        finally:
            content.seek(0)
    except (OSError, ValueError) as error:
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Не удалось прочитать оригинал: ошибка доступа к данным.') from error


def content_size(content: OriginalContent) -> int:
    if isinstance(content, bytes):
        return len(content)
    try:
        content.seek(0, 2)
        size = content.tell()
        content.seek(0)
        return size
    except (OSError, ValueError) as error:
        raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Не удалось прочитать оригинал: ошибка доступа к данным.') from error


def content_digest(content: OriginalContent) -> tuple[str, int]:
    digest, size = sha256(), 0
    for chunk in content_chunks(content):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size
