"""Explicit public response schemas for the HTTP boundary."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExtractionBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    artifact_id: UUID
    artifact_revision: int = Field(ge=1)
    command_key: str = Field(min_length=1, max_length=128)
    module_version: str = '1.0.0'
    dataset_id: UUID | None = None
    expected_revision: int = Field(default=0, ge=0)


class HealthResponse(BaseModel):
    status: Literal['ok']


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


class ArtifactRefResponse(BaseModel):
    artifact_id: UUID
    revision: int = Field(ge=1)


class DatasetRefResponse(BaseModel):
    dataset_id: UUID
    revision: int = Field(ge=1)


class ArtifactMetadataResponse(ArtifactRefResponse):
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


class ProducerResponse(BaseModel):
    module_id: str
    module_version: str
    workflow_type: str
    workflow_version: str


class DatasetEnvelopeResponse(DatasetRefResponse):
    envelope_version: str
    data_type: str
    schema_version: str
    owner_module_id: str
    data_scope: str
    producer: ProducerResponse
    process_run_id: UUID
    input_snapshot_id: UUID
    configuration_revision: int
    created_at: datetime
    observed_at: datetime | None
    quality_state: Literal['COMPLETE', 'PARTIAL', 'CONFLICTED']
    record_count: int


class ArtifactInputResponse(ArtifactRefResponse):
    role: Literal['test_fixture']


class InputSnapshotResponse(BaseModel):
    input_snapshot_id: UUID
    data_scope: str
    inputs: list[ArtifactInputResponse]


class DatasetResponse(DatasetEnvelopeResponse):
    input_snapshot: InputSnapshotResponse


class RecordResponse(BaseModel):
    record_id: str
    quality_state: Literal['COMPLETE', 'PARTIAL', 'CONFLICTED']
    payload: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    issues: list[dict[str, str]]


class RecordsResponse(DatasetRefResponse):
    records: list[RecordResponse]
    next_cursor: int | None


class HistoryResponse(BaseModel):
    artifact_id: UUID
    revisions: list[ArtifactMetadataResponse]
    datasets: list[DatasetEnvelopeResponse]
