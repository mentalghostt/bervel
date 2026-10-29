from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

ArtifactUsageRole = Literal['general', 'procurement', 'stock', 'economics']


@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: UUID


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
    source_role: str = 'test_fixture'


@dataclass(frozen=True)
class Artifact:
    ref: ArtifactRef
    metadata: dict[str, Any]
    content: bytes
