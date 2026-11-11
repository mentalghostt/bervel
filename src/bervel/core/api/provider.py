"""Write-only provider credentials; the runner owns the secret volume."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from bervel.core.models import ActorContext
from bervel.core.services import authorize


class ProviderBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['mox', 'api_key']
    model: str = Field(min_length=1, max_length=200)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: SecretStr | None = Field(default=None, max_length=8192)


class ApiProfile(BaseModel):
    base_url: str
    model: str
    api_key_configured: bool
    error: str | None = None


class MoxProfile(BaseModel):
    base_url: str
    model: str
    credentials_available: bool
    error: str | None = None


class ProviderResponse(BaseModel):
    provider: Literal['mox', 'api_key']
    api: ApiProfile
    mox: MoxProfile
    configured: bool


class TestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TestResponse(BaseModel):
    answer: str
    model: str


def provider_router(client, actor) -> APIRouter:
    router = APIRouter(prefix='/api/v0.1/settings/model', tags=['Настройки модели'])

    @router.get('', response_model=ProviderResponse)
    def read(context: Annotated[ActorContext, Depends(actor)]):
        authorize(context, 'settings:read')
        return client.settings_request('GET', '/v1/settings')

    @router.put('', response_model=ProviderResponse)
    def save(body: ProviderBody, context: Annotated[ActorContext, Depends(actor)]):
        authorize(context, 'settings:write')
        payload = body.model_dump(exclude_none=True, exclude={'api_key'})
        if body.api_key is not None:
            payload['api_key'] = body.api_key.get_secret_value()
        return client.settings_request('PUT', '/v1/settings', payload)

    @router.post('/test', response_model=TestResponse)
    def test(body: TestBody, context: Annotated[ActorContext, Depends(actor)]):
        authorize(context, 'settings:write')
        return client.settings_request('POST', '/v1/settings/test', {})

    return router
