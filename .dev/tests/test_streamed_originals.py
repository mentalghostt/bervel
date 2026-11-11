import hashlib
from io import BytesIO
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.core.infrastructure.storage import FileObjectStore
from bervel.core.models import RegisterArtifact


class BoundedStream(BytesIO):
    def read(self, size=-1):
        assert 0 < size <= 1024 * 1024, 'Originals must be processed in bounded chunks'
        return super().read(size)


def test_streamed_store_preserves_large_original_without_eager_reads(tmp_path):
    data = b'BERVEL-stream\n' * 500000
    store = FileObjectStore(tmp_path)
    stored = store.put(BoundedStream(data))
    assert stored.size == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    with store.open(stored.object_ref) as stream:
        assert hashlib.sha256(stream.read()).hexdigest() == stored.sha256
    assert store.put(BoundedStream(data)) == stored


def test_stream_open_rejects_corruption_before_returning_bytes(tmp_path):
    store = FileObjectStore(tmp_path)
    stored = store.put(b'original')
    digest = stored.sha256
    (tmp_path / digest[:2] / digest).write_bytes(b'corrupted')
    with pytest.raises(CoreError, match='TRANSIENT_DEPENDENCY_ERROR'):
        store.open(stored.object_ref)


def test_stream_registration_keeps_idempotency_and_integrity(services):
    service, repository, store, actor = services
    data = b'large original' * 500000
    key = uuid4().hex
    def command(value):
        return RegisterArtifact(BoundedStream(value), 'large.bin', 'application/octet-stream', {}, key)
    first = service.register(actor, command(data))
    assert service.register(actor, command(data)) == first
    metadata, stream = service.open(actor, first)
    with stream:
        assert stream.read() == data
    assert metadata['size'] == len(data)
    with pytest.raises(CoreError):
        service.register(actor, command(data + b'changed'))


def test_stream_io_errors_keep_storage_failure_status_and_close_handle(tmp_path, monkeypatch):
    from pathlib import Path
    class BrokenStream(BytesIO):
        def read(self, size=-1):
            raise OSError('simulated disk failure')
    store = FileObjectStore(tmp_path)
    with pytest.raises(CoreError, match='TRANSIENT_DEPENDENCY_ERROR'):
        store.put(BrokenStream(b'original'))
    stored = store.put(b'original')
    broken = BrokenStream(b'original')
    actual_open = Path.open
    def open_file(path, *args, **kwargs):
        return broken if path.name == stored.sha256 else actual_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_file)
    with pytest.raises(CoreError, match='TRANSIENT_DEPENDENCY_ERROR'):
        store.open(stored.object_ref)
    assert broken.closed


def test_legacy_read_keeps_storage_failure_status_and_closes_handle(tmp_path, monkeypatch):
    class BrokenRead(BytesIO):
        def read(self, size=-1):
            raise OSError('simulated download failure')
    store = FileObjectStore(tmp_path)
    broken = BrokenRead(b'original')
    monkeypatch.setattr(store, 'open', lambda ref: broken)
    with pytest.raises(CoreError, match='TRANSIENT_DEPENDENCY_ERROR'):
        store.read('sha256:' + 'a' * 64)
    assert broken.closed


def test_registration_rejects_original_changed_between_hash_and_storage(services, monkeypatch):
    service, repository, store, actor = services
    actual_put = store.put
    monkeypatch.setattr(store, 'put', lambda content: actual_put(b'replacement bytes'))
    command = RegisterArtifact(BoundedStream(b'original bytes'), 'changed.bin', 'application/octet-stream', {}, uuid4().hex)
    with pytest.raises(CoreError, match='TRANSIENT_DEPENDENCY_ERROR'):
        service.register(actor, command)
    assert service.list(actor)['artifacts'] == []
