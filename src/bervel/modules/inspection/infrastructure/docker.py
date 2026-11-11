"""Only fixed Docker GET routes; the model never receives a daemon transport."""
from io import BytesIO
import http.client
import json
from pathlib import PurePosixPath
import socket
import struct
import tarfile
from urllib.parse import urlencode, quote

from bervel.core.errors import CoreError
from bervel.modules.inspection.infrastructure.files import MAX_FILE_BYTES, decode, page


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__('docker', timeout=15)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class DockerReads:
    def __init__(self, socket_path: str = '/var/run/docker.sock'):
        self.socket_path = socket_path

    def get(self, path: str, query: dict | None = None, *, raw: bool = False):
        connection = UnixConnection(self.socket_path)
        try:
            connection.request('GET', '/v1.53' + path + ('?' + urlencode(query) if query else ''))
            response = connection.getresponse()
            content = response.read(MAX_FILE_BYTES + 1)
            if response.status != 200:
                raise CoreError('NOT_FOUND' if response.status == 404 else 'INSPECTION_UNAVAILABLE', 'Диагностическое чтение Docker недоступно.')
            if len(content) > MAX_FILE_BYTES:
                raise CoreError('QUOTA_EXCEEDED', 'Ответ Docker превышает32МиБ. Уточните путь или временной диапазон логов.')
            return content if raw else json.loads(content)
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise CoreError('INSPECTION_UNAVAILABLE', 'Не удалось прочитать Docker; изменения не выполнялись.') from error
        finally:
            connection.close()


class InspectionDocker:
    def __init__(self, transport, redactor, *, project: str = 'bervel'):
        self.transport, self.redactor, self.project = transport, redactor, project

    def _containers(self):
        items = self.transport.get('/containers/json', {'all': '1', 'filters': json.dumps({'label': ['com.docker.compose.project=' + self.project]})})
        return [item for item in items if item.get('Labels', {}).get('com.docker.compose.project') == self.project]

    def _owned(self, container: str) -> str:
        for item in self._containers():
            if container == item['Id'] or container in [name.lstrip('/') for name in item.get('Names', [])]:
                return item['Id']
        raise CoreError('FORBIDDEN', 'Контейнер не принадлежит BERVEL.')

    def list(self) -> dict:
        return {'containers': [{'id': item['Id'], 'name': next(iter(item.get('Names', [])), '').lstrip('/'),
            'image': item.get('Image'), 'state': item.get('State')} for item in self._containers()]}

    def inspect(self, *, container: str) -> dict:
        identifier = self._owned(container)
        value = self.transport.get('/containers/' + quote(identifier, safe='') + '/json')
        if value.get('Config', {}).get('Labels', {}).get('com.docker.compose.project') != self.project:
            raise CoreError('FORBIDDEN', 'Принадлежность контейнера изменилась.')
        return self.redactor.clean(value)

    def logs(self, *, container: str, since: str = '', until: str = '', tail: int = 200, offset: int = 0, limit: int = 12000) -> dict:
        identifier = self._owned(container)
        if type(tail) is not int or not 1 <= tail <= 10000:
            raise CoreError('VALIDATION_ERROR', 'Неверное число строк логов.')
        query = {'stdout': '1', 'stderr': '1', 'timestamps': '1', 'tail': str(tail)}
        if since: query['since'] = since
        if until: query['until'] = until
        raw = self.transport.get('/containers/' + quote(identifier, safe='') + '/logs', query, raw=True)
        # Non-TTY Docker logs use multiplexed 8-byte headers; TTY logs are plain text.
        chunks, cursor = [], 0
        if len(raw) >= 8 and raw[0] in (0, 1, 2) and raw[1:4] == b'\0\0\0':
            while cursor + 8 <= len(raw):
                size = struct.unpack('>I', raw[cursor + 4:cursor + 8])[0]
                if cursor + 8 + size > len(raw): raise CoreError('INSPECTION_UNAVAILABLE', 'Получен неполный кадр логов Docker.')
                chunks.append(raw[cursor + 8:cursor + 8 + size]); cursor += 8 + size
            raw = b''.join(chunks)
        value = self.redactor.clean(raw.decode('utf-8', errors='replace'))
        return {'container': identifier, 'source': 'logs', 'tail': tail, 'since': since, 'until': until,
            'coverage': 'Последние строки выбранного диапазона; это не вся история контейнера.', **page(value, offset=offset, limit=limit)}

    def files(self, *, container: str, path: str, offset: int = 0, limit: int = 12000) -> dict:
        identifier = self._owned(container)
        parsed = PurePosixPath(path)
        if not path.startswith('/') or '..' in parsed.parts or '\0' in path or '\\' in path:
            raise CoreError('FORBIDDEN', 'Укажите абсолютный путь внутри контейнера без переходов к родителю.')
        if parsed.parts[:2] in [('/', 'proc'), ('/', 'sys'), ('/', 'dev')]:
            raise CoreError('FORBIDDEN', 'Виртуальные и специальные файлы не читаются.')
        raw = self.transport.get('/containers/' + quote(identifier, safe='') + '/archive', {'path': path}, raw=True)
        try:
            with tarfile.open(fileobj=BytesIO(raw), mode='r:*') as archive:
                entries = archive.getmembers()
                if len(entries) == 1 and entries[0].isfile():
                    member = entries[0]
                    if member.size > MAX_FILE_BYTES: raise CoreError('QUOTA_EXCEEDED', 'Файл контейнера превышает32МиБ.')
                    stream = archive.extractfile(member)
                    value, status = decode(stream.read(MAX_FILE_BYTES + 1), member.name)
                    return {'container': identifier, 'path': path, 'status': status,
                        **page(self.redactor.source(value, path), offset=offset, limit=limit)}
                listing = '\n'.join(f"{'directory' if item.isdir() else 'file' if item.isfile() else 'link/special'} {item.name} ({item.size} bytes)" for item in entries)
                return {'container': identifier, 'path': path, 'status': 'directory', **page(self.redactor.clean(listing), offset=offset, limit=limit)}
        except (tarfile.TarError, OSError, ValueError) as error:
            raise CoreError('INSPECTION_UNAVAILABLE', 'Не удалось прочитать архив файлов контейнера.') from error
