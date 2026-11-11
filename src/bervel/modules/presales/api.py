"""Typed HTTP commands and read-only projections for customer requests."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from bervel.core.models import ActorContext
from bervel.modules.presales.service import PresalesService


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command_key: str = Field(min_length=1, max_length=128)


class SettingsValue(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    target_margin_percent: str
    special_min_margin_percent: str
    seller_contacts: str
    offer_terms: str


class SettingsCommand(Command):
    base_snapshot_id: str | None
    settings: SettingsValue


class SettingsResponse(BaseModel):
    snapshot_id: str | None
    settings: SettingsValue
    requires_save: bool


class CreateRequest(Command):
    artifact_ids: list[str] = Field(min_length=1)


class RevisionCommand(Command):
    kind: Literal['manager_update', 'reanalyze']
    base_revision_id: str
    decisions: dict = Field(default_factory=dict)


class Receipt(BaseModel):
    request_id: str
    revision_id: str
    job_id: str
    state: Literal['queued', 'running', 'stopped', 'failed', 'complete']


class JobResponse(BaseModel):
    job_id: str
    state: str
    phase: str
    error: dict | None
    stop_requested: bool
    progress: dict


class RequestSummary(BaseModel):
    request_id: str
    title: str
    client: str | None
    current_revision_id: str
    created_at: str
    updated_at: str
    state: str
    phase: str


class HistoryResponse(BaseModel):
    items: list[RequestSummary]
    next_cursor: int | None


class RequestResponse(BaseModel):
    request: RequestSummary
    revisions: list[dict]
    job: JobResponse | None


class RevisionResponse(BaseModel):
    revision_id: str
    request_id: str
    revision_number: int
    parent_revision_id: str | None
    created_at: str
    evaluated_at: str
    state: str
    phase: str
    settings_snapshot_id: str | None
    company_snapshot_id: str | None
    sources: list[dict]
    extracted: dict
    metrics: dict | None
    review: dict | None
    score: int | None
    model_error: dict | None
    offer: dict | None
    artifacts: dict
    warnings: list[str]
    decisions: dict
    job: JobResponse | None


class LinesResponse(BaseModel):
    items: list[dict]
    next_cursor: int | None


def presales_router(service: PresalesService, actor) -> APIRouter:
    router = APIRouter(prefix='/api/v0.1/presales', tags=['Пресейл-конвейер'])
    Context = Annotated[ActorContext, Depends(actor)]

    @router.get('/settings', response_model=SettingsResponse)
    def read_settings(context: Context):
        return service.read_settings(context)

    @router.put('/settings', response_model=SettingsResponse)
    def save_settings(body: SettingsCommand, context: Context):
        return service.save_settings(context, body.base_snapshot_id, body.settings.model_dump(), body.command_key)

    @router.get('/requests', response_model=HistoryResponse)
    def history(context: Context, cursor: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=100)] = 30):
        return service.list_requests(context, cursor, limit)

    @router.post('/requests', status_code=202, response_model=Receipt)
    def create(body: CreateRequest, context: Context):
        return service.create_request(context, body.artifact_ids, body.command_key)

    @router.get('/requests/{request_id}', response_model=RequestResponse)
    def read(request_id: UUID, context: Context):
        return service.read_request(context, request_id)

    @router.get('/requests/{request_id}/revisions/{revision_id}', response_model=RevisionResponse)
    def revision(request_id: UUID, revision_id: UUID, context: Context):
        return service.read_revision(context, request_id, revision_id)

    @router.get('/requests/{request_id}/revisions/{revision_id}/lines', response_model=LinesResponse)
    def read_lines(request_id: UUID, revision_id: UUID, context: Context,
                   cursor: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50,
                   list_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None):
        return service.list_lines(context, request_id, revision_id, cursor, limit, list_id=list_id)

    @router.post('/requests/{request_id}/revisions', status_code=202, response_model=Receipt)
    def create_revision(request_id: UUID, body: RevisionCommand, context: Context):
        return service.create_revision(context, request_id, body.base_revision_id,
                                       body.kind, body.decisions, body.command_key)

    @router.post('/requests/{request_id}/jobs/{job_id}/stop', response_model=Receipt)
    def stop(request_id: UUID, job_id: UUID, body: Command, context: Context):
        return service.stop_job(context, request_id, job_id, body.command_key)

    @router.post('/requests/{request_id}/jobs/{job_id}/resume', status_code=202, response_model=Receipt)
    def resume(request_id: UUID, job_id: UUID, body: Command, context: Context):
        return service.resume_job(context, request_id, job_id, body.command_key)

    return router
