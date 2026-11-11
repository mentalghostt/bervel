"""Named read-only roots; never follow paths outside the selected root."""
import mimetypes
import os
from pathlib import Path, PureWindowsPath
import stat

from bervel.core.errors import CoreError
from bervel.core.infrastructure.document_reader import DocumentReader

MAX_FILE_BYTES = 32 * 1024 * 1024


def page(text: str, *, offset: int = 0, limit: int = 12000) -> dict:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 32000:
        raise CoreError('VALIDATION_ERROR', 'Неверная страница чтения.')
    end = min(len(text), offset + limit)
    return {'text': text[offset:end], 'offset': offset, 'next_offset': end if end < len(text) else None,
        'total_characters': len(text), 'line_start': text.count('\n', 0, offset) + 1,
        'line_end': text.count('\n', 0, end) + 1, 'partial': end < len(text)}


def decode(content: bytes, filename: str) -> tuple[str, str]:
    try:
        value = content.decode('utf-8-sig')
        if '\0' not in value and not any(ord(c) < 32 and c not in '\r\n\t' for c in value): return value, 'ready'
    except UnicodeError:
        pass
    result = DocumentReader().extract(content, filename, mimetypes.guess_type(filename)[0] or 'application/octet-stream')
    return '\n'.join(f"[{chunk['unit']} {chunk['row_start']}–{chunk['row_end']}]\n{chunk['excerpt']}" for chunk in result['chunks']), result['status']


class InspectionFiles:
    def __init__(self, roots: dict[str, Path], redactor):
        self.roots, self.redactor = {name: root.resolve() for name, root in roots.items()}, redactor
        project = self.roots.get('project')
        if project and (project / '.env').is_file():
            self.redactor.observe((project / '.env').read_text(encoding='utf-8', errors='replace'))

    def _path(self, root: str, path: str) -> Path:
        base = self.roots.get(root)
        if base is None or not isinstance(path, str) or '\0' in path or '\\' in path or PureWindowsPath(path).drive or Path(path).is_absolute():
            raise CoreError('FORBIDDEN', 'Источник находится вне разрешённого корня BERVEL.')
        target = (base / path).resolve()
        if not target.is_relative_to(base): raise CoreError('FORBIDDEN', 'Выход за корень BERVEL запрещён.')
        if not target.exists(): raise CoreError('NOT_FOUND', 'Файл или каталог не найден.')
        return target

    def _open(self, root: str, target: Path) -> int:
        # Resolve and open are separate operations. Walk anchored directory handles
        # so replacing any intermediate component with a symlink cannot escape.
        descriptor = os.open(self.roots[root], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parts = target.relative_to(self.roots[root]).parts
            if not parts: return os.dup(descriptor)
            for part in parts[:-1]:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor); descriptor = following
            return os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=descriptor)
        except OSError as error:
            raise CoreError('FORBIDDEN', 'Путь изменился или содержит небезопасную ссылку.') from error
        finally:
            os.close(descriptor)

    def list(self, *, root: str = 'project', path: str = '.', offset: int = 0, limit: int = 100) -> dict:
        target = self._path(root, path)
        if not target.is_dir(): raise CoreError('VALIDATION_ERROR', 'Ожидался каталог.')
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 200:
            raise CoreError('VALIDATION_ERROR', 'Неверная страница каталога.')
        descriptor = self._open(root, target)
        entries = []
        try:
            items = sorted(os.listdir(descriptor), key=str.casefold)
            for name in items[offset:offset + limit]:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                entries.append({'name': name, 'path': str((target / name).relative_to(self.roots[root])),
                'kind': 'symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'special',
                'size': info.st_size})
        finally:
            os.close(descriptor)
        return {'root': root, 'path': path, 'roots': list(self.roots), 'entries': entries,
            'total': len(items), 'offset': offset, 'next_offset': offset + limit if offset + limit < len(items) else None}

    def read(self, *, root: str = 'project', path: str, offset: int = 0, limit: int = 12000) -> dict:
        target = self._path(root, path)
        try:
            descriptor = self._open(root, target)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode): raise CoreError('FORBIDDEN', 'Специальные файлы не читаются.')
                content = stream.read(MAX_FILE_BYTES + 1)
        except OSError as error:
            raise CoreError('FORBIDDEN', 'Файл недоступен для безопасного чтения.') from error
        if len(content) > MAX_FILE_BYTES:
            raise CoreError('QUOTA_EXCEEDED', 'Файл превышает32МиБ; содержимое не прочитано. Для логов задайте временной диапазон.')
        value, status = decode(content, target.name)
        return {'root': root, 'path': path, 'size': info.st_size, 'status': status,
            **page(self.redactor.source(value, str(target.relative_to(self.roots[root]))), offset=offset, limit=limit)}
