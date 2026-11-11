from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext
from bervel.core.services import authorize
from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient, verified_limit_groups
from bervel.modules.ai_tender_radar.source_contract import SIMULATOR_KEY


class KonturSettingsBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    api_key: SecretStr = Field(min_length=1, max_length=1024)


class LimitGroupResponse(BaseModel):
    group: str
    remaining: int
    maximum: int


class KonturSettingsResponse(BaseModel):
    configured: bool
    limits: list[LimitGroupResponse]


class SimulatorQuotaResponse(BaseModel):
    available: bool
    unlimited: bool
    resettable: bool
    limits: list[LimitGroupResponse]


def _read_live_limits(key: str) -> list[dict]:
    return KonturClient(key, allow_configured_api=True).read_limits()


def kontur_settings_router(credentials, actor, *, read_limits=_read_live_limits, simulator=None) -> APIRouter:
    router = APIRouter(prefix='/api/v0.1/settings/kontur', tags=['Контур.Закупки'])

    def settings(key: str) -> KonturSettingsResponse:
        limits = verified_limit_groups(read_limits(key)) if key else {}
        return KonturSettingsResponse(configured=bool(key), limits=[
            LimitGroupResponse(group=group, remaining=limit['AvailableRequests'], maximum=limit['TotalLimit'])
            for group, limit in limits.items()])

    @router.get('', response_model=KonturSettingsResponse)
    def read(context: Annotated[ActorContext, Depends(actor)], response: Response):
        authorize(context, 'settings:read')
        response.headers['Cache-Control'] = 'no-store'
        return settings(credentials.read())

    @router.put('', response_model=KonturSettingsResponse)
    def save(body: KonturSettingsBody, context: Annotated[ActorContext, Depends(actor)], response: Response):
        authorize(context, 'settings:write')
        response.headers['Cache-Control'] = 'no-store'
        key = body.api_key.get_secret_value().strip()
        if not key or key == SIMULATOR_KEY or any(not 33 <= ord(char) <= 126 for char in key):
            raise CoreError('VALIDATION_ERROR', 'Проверьте ключ Контур.Закупок.')
        result = settings(key)
        credentials.save(key)
        return result

    def simulator_quota(rows=None) -> SimulatorQuotaResponse:
        if simulator is None or not getattr(simulator, 'simulated', False):
            return SimulatorQuotaResponse(available=False, unlimited=False, resettable=False, limits=[])
        limits = verified_limit_groups(simulator.read_limits() if rows is None else rows)
        return SimulatorQuotaResponse(available=True, unlimited=not limits,
            resettable=getattr(simulator, 'provider_limits', None) is None, limits=[
                LimitGroupResponse(group=group, remaining=limit['AvailableRequests'], maximum=limit['TotalLimit'])
                for group, limit in limits.items()])

    @router.get('/simulation/quota', response_model=SimulatorQuotaResponse)
    def read_simulator(context: Annotated[ActorContext, Depends(actor)], response: Response):
        authorize(context, 'settings:read')
        response.headers['Cache-Control'] = 'no-store'
        return simulator_quota()

    @router.post('/simulation/quota/reset', response_model=SimulatorQuotaResponse)
    def reset_simulator(context: Annotated[ActorContext, Depends(actor)], response: Response):
        authorize(context, 'settings:write')
        response.headers['Cache-Control'] = 'no-store'
        if simulator is None or not getattr(simulator, 'simulated', False):
            raise CoreError('KONTUR_UNCONFIGURED', 'Симуляция API не настроена.')
        return simulator_quota(simulator.reset_simulator_quota())

    return router
