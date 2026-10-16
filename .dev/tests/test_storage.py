from concurrent.futures import ThreadPoolExecutor
import hashlib
import pytest


def test_original_bytes_and_concurrent_deduplication(tmp_path):
    from bervel.infrastructure.storage import FileObjectStore
    store = FileObjectStore(tmp_path)
    content = b'\xef\xbb\xbforiginal\r\n'
    with ThreadPoolExecutor(max_workers=4) as executor:
        objects = list(executor.map(store.put, [content] * 4))
    assert len({o.object_ref for o in objects}) == 1
    assert objects[0].sha256 == hashlib.sha256(content).hexdigest()
    assert store.read(objects[0].object_ref) == content
    assert not objects[0].object_ref.startswith(str(tmp_path))


def test_corruption_and_traversal_are_not_returned(tmp_path):
    from bervel.infrastructure.storage import FileObjectStore
    from bervel.core.errors import CoreError
    store = FileObjectStore(tmp_path)
    obj = store.put(b'original')
    for file in tmp_path.rglob('*'):
        if file.is_file():
            file.write_bytes(b'broken')
    with pytest.raises(CoreError, match='TRANSIENT_DEPENDENCY_ERROR'):
        store.read(obj.object_ref)
    with pytest.raises(CoreError):
        store.read('../../outside')


def test_file_write_failure_is_explicit(tmp_path):
    from bervel.infrastructure.storage import FileObjectStore
    from bervel.core.errors import CoreError
    path = tmp_path / 'not-a-directory'
    path.write_bytes(b'x')
    with pytest.raises(CoreError, match='TRANSIENT_DEPENDENCY_ERROR'):
        FileObjectStore(path).put(b'original')
