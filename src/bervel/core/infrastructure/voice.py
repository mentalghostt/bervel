"""Internal voice HTTP adapter; credentials belong only to the runner."""
import asyncio
import base64
import json
import socket
from threading import Event
from http.client import HTTPConnection, HTTPSConnection, HTTPException
from urllib.parse import urlsplit

from bervel.core.errors import CoreError

MAX_AUDIO_BYTES = 5 * 1024 * 1024
VOICE_MODEL = 'whisper-large-v3-turbo'
VOICE_ERRORS = {
    'VOICE_UNCONFIGURED': 'Добавьте ключ Groq в настройках голоса.',
    'VOICE_SETTINGS_UNAVAILABLE': 'Хранилище голосового доступа недоступно.',
    'INVALID_VOICE_SETTINGS': 'Проверьте ключ Groq.',
    'VOICE_UNSUPPORTED_AUDIO': 'Этот формат аудио не поддерживается.',
    'VOICE_INVALID_AUDIO': 'Некорректная аудиозапись.',
    'VOICE_TOO_LARGE': 'Аудиозапись превышает 5 МиБ.',
    'VOICE_AUTH_ERROR': 'Groq отклонил ключ доступа. Проверьте настройки голоса.',
    'VOICE_BUSY': 'Лимит распознавания Groq исчерпан. Попробуйте позже.',
    'VOICE_TIMEOUT': 'Распознавание прервано или превысило время ожидания.',
    'VOICE_FAILED': 'Не удалось распознать речь.',
}


class VoiceClient:
    def __init__(self, url: str):
        self.url = url.rstrip('/')

    def settings_request(self, method: str, path: str, body: dict | None = None) -> dict:
        result = self._request(method, path, body)
        if (type(result.get('configured')) is not bool or result.get('state') not in ('configured', 'unconfigured')
                or result.get('model') != VOICE_MODEL or result['configured'] != (result['state'] == 'configured')):
            raise CoreError('VOICE_FAILED', VOICE_ERRORS['VOICE_FAILED'])
        return {key: result[key] for key in ('configured', 'state', 'model')}

    async def transcribe(self, audio: bytes, mime_type: str, filename: str) -> dict[str, str]:
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            code = 'VOICE_TOO_LARGE' if len(audio) > MAX_AUDIO_BYTES else 'VOICE_INVALID_AUDIO'
            raise CoreError(code, VOICE_ERRORS[code])
        payload = {'audio_base64': base64.b64encode(audio).decode('ascii'), 'mime_type': mime_type, 'filename': filename}
        # Close the active socket on cancellation, so the runner sees disconnect and aborts Groq.
        connection_holder = {'cancelled': Event(), 'connection': None}
        try:
            result = await asyncio.to_thread(self._request, 'POST', '/v1/transcribe', payload, connection_holder)
        except asyncio.CancelledError:
            connection_holder['cancelled'].set()
            connection = connection_holder['connection']
            if connection is not None:
                if connection.sock:
                    try:
                        connection.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass  # Peer may already have closed the cancelled connection.
                connection.close()
            raise
        if not isinstance(result.get('text'), str) or len(result['text']) > 32000:
            raise CoreError('VOICE_FAILED', VOICE_ERRORS['VOICE_FAILED'])
        return {'text': result['text']}

    def _request(self, method: str, path: str, body: dict | None = None, connection_holder: dict | None = None) -> dict:
        url = urlsplit(self.url)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise CoreError('VOICE_FAILED', VOICE_ERRORS['VOICE_FAILED'])
        factory = HTTPSConnection if url.scheme == 'https' else HTTPConnection
        connection = factory(url.hostname, url.port, timeout=65)
        if connection_holder is not None:
            connection_holder['connection'] = connection
        try:
            if connection_holder is not None:
                if connection_holder['cancelled'].is_set():
                    raise OSError('Cancelled')
                connection.connect()
                if connection_holder['cancelled'].is_set():
                    raise OSError('Cancelled')
            payload = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
            connection.request(method, url.path.rstrip('/') + path, body=payload, headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            content = response.read(256 * 1024 + 1)
            if len(content) > 256 * 1024:
                raise ValueError('Response limit')
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError('Response type')
            if not 200 <= response.status < 300:
                error = result.get('error')
                code = error.get('code') if isinstance(error, dict) else None
                if not isinstance(code, str) or code not in VOICE_ERRORS:
                    code = 'VOICE_FAILED'
                raise CoreError(code, VOICE_ERRORS[code])
            return result
        except TimeoutError as error:
            raise CoreError('VOICE_TIMEOUT', VOICE_ERRORS['VOICE_TIMEOUT']) from error
        except (OSError, HTTPException, ValueError) as error:
            raise CoreError('VOICE_FAILED', VOICE_ERRORS['VOICE_FAILED']) from error
        finally:
            connection.close()
