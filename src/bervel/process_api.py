from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from .api_models import DatasetRefResponse, ErrorResponse, ExtractionBody
from .core.errors import CoreError
from .core.models import ActorContext, ArtifactRevisionRef, ExtractArtifact
from .core.process_models import RecoverProcess


class RecoveryBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command_key: str = Field(min_length=1, max_length=128)
    expected_attempt: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class CallbackBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    execution_id: str = Field(min_length=1, max_length=128, pattern=r'^[0-9]+$')
    invocation_token: str = Field(min_length=1, max_length=128, repr=False)


class ProcessAttemptResponse(BaseModel):
    number: int
    execution_id: str | None
    parent_execution_id: str | None
    state: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class ProcessResponse(BaseModel):
    process_run_id: UUID
    data_scope: str
    state: Literal['QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'RECOVERY_REQUIRED']
    active_attempt: int
    input: dict
    producer: dict[str, str]
    workflow: dict[str, str]
    result: DatasetRefResponse | None
    created_at: datetime
    updated_at: datetime
    attempts: list[ProcessAttemptResponse]


class ProcessListResponse(BaseModel):
    processes: list[ProcessResponse]
    next_cursor: int | None


class ClaimResponse(BaseModel):
    process_run_id: UUID
    execution_id: str
    state: str


def process_routers(service, actor, worker):
    responses = {status: {'model': ErrorResponse} for status in (401, 403, 404, 409, 413, 422, 500, 503)}
    router = APIRouter(prefix='/api/v0.1', tags=['Процессы'], dependencies=[Depends(actor)], responses=responses)
    internal = APIRouter(prefix='/api/internal/v0.1', tags=['Служебные процессы'],
                         dependencies=[Depends(worker)], responses=responses)

    def configured():
        if service is None:
            raise CoreError('TRANSIENT_DEPENDENCY_ERROR', 'Orchestration is not configured; the synchronous API remains available')
        return service

    @router.post('/demo/document-review/processes', status_code=202, response_model=ProcessResponse)
    def start(body: ExtractionBody, context: Annotated[ActorContext, Depends(actor)]):
        return configured().start(context, ExtractArtifact(ArtifactRevisionRef(body.artifact_id, body.artifact_revision),
            body.command_key, body.module_version, body.dataset_id, body.expected_revision))

    @router.get('/processes', response_model=ProcessListResponse)
    def list_processes(context: Annotated[ActorContext, Depends(actor)],
                      state: str | None = None, cursor: Annotated[int, Query(ge=0)] = 0,
                      limit: Annotated[int, Query(ge=1, le=100)] = 20):
        return configured().list(context, state, cursor, limit)

    @router.get('/processes/{run_id}', response_model=ProcessResponse)
    def read(run_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        return configured().read(context, run_id)

    @router.post('/processes/{run_id}/reconcile', response_model=ProcessResponse)
    def reconcile(run_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        return configured().reconcile(context, run_id)

    @router.post('/processes/{run_id}/recover', status_code=202, response_model=ProcessResponse)
    def recover(run_id: UUID, body: RecoveryBody, context: Annotated[ActorContext, Depends(actor)]):
        return configured().recover(context, run_id, RecoverProcess(body.command_key, body.expected_attempt, body.reason))

    @internal.post('/processes/{run_id}/claim', response_model=ClaimResponse)
    def claim(run_id: UUID, body: CallbackBody):
        return configured().claim(run_id, body.execution_id, body.invocation_token)

    @internal.post('/processes/{run_id}/execute', response_model=DatasetRefResponse)
    def execute(run_id: UUID, body: CallbackBody):
        return configured().execute(run_id, body.execution_id, body.invocation_token)

    return router, internal
