"""Explicit public response schemas for the HTTP boundary."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from bervel.core.models import ArtifactUsageRole


class HealthResponse(BaseModel):
    status: Literal['ok']


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ArtifactRefResponse(BaseModel):
    artifact_id: UUID


class ArtifactPreparation(BaseModel):
    usage_role: ArtifactUsageRole = 'general'
    processing_state: Literal['pending','running','ready','partial','unsupported','failed'] = 'pending'
    completed_chunks: int = Field(default=0,ge=0)
    total_chunks: int = Field(default=0,ge=0)
    processing_error: str | None = None


class ArtifactUsageBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    usage_role: ArtifactUsageRole


class ArtifactUsageResponse(BaseModel):
    artifact_id: UUID
    usage_role: ArtifactUsageRole


class ArtifactMetadataResponse(ArtifactRefResponse, ArtifactPreparation):
    object_ref: str
    sha256: str
    size: int
    filename: str
    media_type: str
    source: dict[str, str | None]
    source_role: Literal['test_fixture']
    data_scope: str
    owner_module_id: str
    created_at: datetime


class ArtifactListItemResponse(ArtifactRefResponse, ArtifactPreparation):
    filename: str
    media_type: str
    size: int = Field(ge=1)
    created_at: datetime


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactListItemResponse]
    next_cursor: int | None

