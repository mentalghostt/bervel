from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from .models import ActorContext, ExtractArtifact


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_id: str
    version_id: str
    workflow_version: str


@dataclass(frozen=True)
class EngineExecution:
    execution_id: str
    workflow_id: str
    version_id: str
    state: str
    retry_of: str | None = None
    retry_success_id: str | None = None
    process_run_id: str | None = None
    invocation_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RecoverProcess:
    command_key: str
    expected_attempt: int
    reason: str


@dataclass(frozen=True)
class AttemptLease:
    process_run_id: UUID
    attempt: int
    execution_id: str
    actor: ActorContext
    command: ExtractArtifact
    producer: dict[str, str]
    result: dict[str, Any] | None = None
