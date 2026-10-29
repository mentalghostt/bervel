"""Voice creates an editable transcript; it never sends a chat message."""
import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from bervel.core.models import ActorContext
from bervel.core.errors import CoreError
from bervel.core.services import authorize

MAX_AUDIO_BYTES = 5 * 1024 * 1024
SUPPORTED_MIME = frozenset({'audio/webm', 'audio/opus', 'audio/ogg', 'audio/mp4', 'audio/x-m4a',
                          'video/mp4', 'audio/wav', 'audio/x-wav', 'audio/mpeg'})


class VoiceSettingsBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr = Field(max_length=8192)

    @field_validator('api_key')
    @classmethod
    def valid_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value()
        if any(ord(char) < 32 or ord(char) == 127 for char in key) or any(char.isspace() for char in key.strip()):
            raise ValueError('Проверьте ключ xAI.')
        return value


class VoiceSettingsResponse(BaseModel):
    configured: bool
    state: Literal['configured', 'unconfigured']
    model: Literal['grok-voice-transcribe-2.0']


class TranscriptResponse(BaseModel):
    text: str = Field(max_length=32000)


def voice_router(client, actor) -> APIRouter:
    router = APIRouter(prefix='/api/v0.1/voice', tags=['Голос'])

    @router.get('/settings', response_model=VoiceSettingsResponse)
    def read(context: Annotated[ActorContext, Depends(actor)]):
        authorize(context, 'settings:read')
        return client.settings_request('GET', '/v1/voice/settings')

    @router.put('/settings', response_model=VoiceSettingsResponse)
    def save(body: VoiceSettingsBody, context: Annotated[ActorContext, Depends(actor)]):
        authorize(context, 'settings:write')
        return client.settings_request('PUT', '/v1/voice/settings', {'api_key': body.api_key.get_secret_value()})

    @router.post('/transcribe', response_model=TranscriptResponse)
    async def transcribe(request: Request, file: UploadFile, context: Annotated[ActorContext, Depends(actor)]):
        authorize(context, 'chat:write')
        mime = (file.content_type or '').split(';', 1)[0].strip().lower()
        if mime not in SUPPORTED_MIME:
            raise CoreError('VOICE_UNSUPPORTED_AUDIO', 'Этот формат аудио не поддерживается.')
        filename = file.filename or 'recording.audio'
        if len(filename) > 200 or any(char in '/\\' or ord(char) < 32 or ord(char) == 127 for char in filename):
            raise CoreError('VOICE_INVALID_AUDIO', 'Некорректное имя аудиозаписи.')
        audio = await file.read(MAX_AUDIO_BYTES + 1)
        await file.close()
        if not audio or len(audio) > MAX_AUDIO_BYTES:
            code = 'VOICE_TOO_LARGE' if audio else 'VOICE_INVALID_AUDIO'
            raise CoreError(code, 'Аудиозапись должна содержать от 1 байта до 5 МиБ.')
        task = asyncio.create_task(client.transcribe(audio, mime, filename))
        try:
            while not task.done():
                if await request.is_disconnected():
                    task.cancel()
                    raise CoreError('VOICE_CANCELLED', 'Распознавание отменено.')
                await asyncio.wait({task}, timeout=0.1)
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return router
