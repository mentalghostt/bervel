from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class ArtifactRevisionRef:
    artifact_id: UUID
    revision: int


@dataclass(frozen=True)
class DataRecord:
    record_id: str
    quality_state: str
    payload: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    issues: list[dict[str, str]]


@dataclass(frozen=True)
class ExtractionResult:
    schema_id: str
    schema_version: str
    quality_state: str
    records: list[DataRecord]


@dataclass(frozen=True)
class ActorContext:
    actor_id: str
    data_scope: str
    permissions: frozenset[str]


@dataclass(frozen=True)
class StoredObject:
    object_ref: str
    sha256: str
    size: int


@dataclass(frozen=True)
class RegisterArtifact:
    content: bytes
    filename: str
    media_type: str
    source: dict[str, Any]
    command_key: str
    artifact_id: UUID | None = None
    expected_revision: int = 0
    source_role: str = 'test_fixture'


@dataclass(frozen=True)
class ArtifactRevision:
    ref: ArtifactRevisionRef
    metadata: dict[str, Any]
    content: bytes


@dataclass(frozen=True)
class DatasetRevisionRef:
    dataset_id: UUID
    revision: int


@dataclass(frozen=True)
class ExtractArtifact:
    artifact_ref: ArtifactRevisionRef
    command_key: str
    module_version: str = '1.0.0'
    dataset_id: UUID | None = None
    expected_revision: int = 0
