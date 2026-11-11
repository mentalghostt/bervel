from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from bervel.core.models import ActorContext
from bervel.modules.company_settings.models import Snapshot
from bervel.modules.company_settings.policy import EffectiveScoringPolicy, effective_scoring_policy
from bervel.modules.company_settings.service import CompanySettings


class StrictBody(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ProductBody(StrictBody):
    id: str
    sku: str | None
    name: str
    unit: str
    attributes: dict[str,str]
    enabled: StrictBool
    manufactured_before: StrictBool | None = None
    evidence: str | None = None


class ExperimentalBody(StrictBody):
    id: str
    product_id: str | None = None
    sku: str | None = None
    name: str
    unit: str
    attributes: dict[str,str]
    enabled: StrictBool


class CapabilityBody(StrictBody):
    product_id: str
    resource_group_id: str
    units_per_workday: str | None
    production_unit_cost: str | None
    currency: str | None
    setup_workdays: str
    setup_cost: str | None = None
    material_ready_at: str | None
    confirmed_at: str | None = None
    evidence: str | None = None


class ResourceBody(StrictBody):
    group_id: str
    calendar: list[StrictInt]
    enabled: StrictBool = True
    free_capacity_by_day: dict[str,str] = Field(default_factory=dict)
    confirmed_at: str | None = None
    default_daily_capacity: str | None = None


class ProfileBody(StrictBody):
    stock_artifact_id: str
    stock_sha256: str
    products: list[ProductBody]
    experimental_products: list[ExperimentalBody]
    include_experimental: StrictBool
    production: list[CapabilityBody]
    resources: list[ResourceBody]
    scoring_policy: dict[str,Any]

    def to_profile(self) -> dict:
        # Exclude unset optional fields; explicit null evidence is treated as unknown.
        result = self.model_dump(exclude_unset=True)
        for row in result['products'] + result['production']:
            if row.get('evidence') is None:
                row.pop('evidence',None)
        for row in result['resources']:
            if row.get('default_daily_capacity') is None:
                row.pop('default_daily_capacity',None)
        return result


class PreviewBody(StrictBody):
    stock_artifact_id: UUID
    base_snapshot_id: UUID | None = None


class SaveBody(StrictBody):
    command_key: str = Field(min_length=1,max_length=128)
    base_snapshot_id: UUID | None
    profile: ProfileBody


class SnapshotResponse(StrictBody):
    id: UUID
    created_at: datetime
    profile: ProfileBody
    effective_scoring_policy: EffectiveScoringPolicy


class SettingsResponse(StrictBody):
    snapshot: SnapshotResponse | None


class PreviewResponse(StrictBody):
    profile: ProfileBody
    warnings: list[str]


def _settings_response(snapshot: Snapshot | None) -> SettingsResponse:
    if snapshot is None:
        return SettingsResponse(snapshot=None)
    view = SnapshotResponse(**snapshot, effective_scoring_policy=effective_scoring_policy(snapshot['profile']['scoring_policy']))
    return SettingsResponse(snapshot=view)


def company_settings_router(service: CompanySettings, actor: Callable[...,ActorContext]) -> APIRouter:
    router = APIRouter(prefix='/api/v0.1/settings/company')

    @router.get('',response_model_exclude_unset=True)
    def read(context: Annotated[ActorContext,Depends(actor)]) -> SettingsResponse:
        return _settings_response(service.read(context))

    @router.post('/preview',response_model_exclude_unset=True)
    def preview(body: PreviewBody,context: Annotated[ActorContext,Depends(actor)]) -> PreviewResponse:
        return PreviewResponse(**service.preview(context,body.stock_artifact_id,body.base_snapshot_id))

    @router.put('',response_model_exclude_unset=True)
    def save(body: SaveBody,context: Annotated[ActorContext,Depends(actor)]) -> SettingsResponse:
        return _settings_response(service.save(context,body.base_snapshot_id,body.profile.to_profile(),body.command_key))

    return router
