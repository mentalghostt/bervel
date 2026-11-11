from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID
from bervel.core.originals import OriginalContent

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
    content: OriginalContent
    filename: str
    media_type: str
    source: dict[str, Any]
    command_key: str
    source_role: str = 'test_fixture'
    owner_module_id: str = 'demo.document-review'


@dataclass(frozen=True)
class Artifact:
    ref: ArtifactRef
    metadata: dict[str, Any]
    content: bytes
