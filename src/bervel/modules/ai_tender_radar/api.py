from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from bervel.core.models import ActorContext, ArtifactRef
from bervel.modules.ai_tender_radar import AiTenderRadar
from bervel.modules.ai_tender_radar.models import CardPage, CardSort, JobState, JobView


class StockCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    stock_artifact_id: UUID
    command_key: str = Field(min_length=1, max_length=128)


class IntakeBody(StockCommand):
    search_phrase: str = Field(min_length=1, max_length=500)


class ArchiveBody(StockCommand):
    source_artifact_id: UUID


class IntakeReceipt(BaseModel):
    job_id: UUID
    state: JobState


class ArchiveReceipt(IntakeReceipt):
    total: int


def ai_tender_radar_router(service: AiTenderRadar, actor: Callable[..., ActorContext]) -> APIRouter:
    router = APIRouter(prefix='/api/v0.1/ai-tender-radar')

    @router.post('/jobs', status_code=202)
    def enqueue(body: IntakeBody, context: Annotated[ActorContext, Depends(actor)]) -> IntakeReceipt:
        job = service.enqueue(context, ArtifactRef(body.stock_artifact_id), body.search_phrase, body.command_key)
        return IntakeReceipt(job_id=job['job_id'], state=job['state'])

    @router.post('/jobs/from-file', status_code=202)
    def enqueue_archive(body: ArchiveBody, context: Annotated[ActorContext, Depends(actor)]) -> ArchiveReceipt:
        job, total = service.enqueue_archive(
            context, ArtifactRef(body.stock_artifact_id), ArtifactRef(body.source_artifact_id), body.command_key)
        return ArchiveReceipt(job_id=job['job_id'], state=job['state'], total=total)

    @router.get('/jobs/active')
    def active(context: Annotated[ActorContext, Depends(actor)]) -> JobView | None:
        return service.active(context)

    @router.get('/jobs/{job_id}')
    def read(job_id: UUID, context: Annotated[ActorContext, Depends(actor)]) -> JobView:
        return service.read(context, job_id)

    @router.get('/cards')
    def cards(context: Annotated[ActorContext, Depends(actor)],
              limit: Annotated[int, Query(ge=1, le=50)] = 30,
              offset: Annotated[int, Query(ge=0)] = 0, sort: CardSort | None = None) -> CardPage:
        return service.cards(context, limit, offset, sort)

    @router.post('/cards/{job_id}/reanalyze', status_code=202)
    def reanalyze(job_id: UUID, body: StockCommand, context: Annotated[ActorContext, Depends(actor)]) -> IntakeReceipt:
        job = service.reanalyze(context, job_id, ArtifactRef(body.stock_artifact_id), body.command_key)
        return IntakeReceipt(job_id=job['job_id'], state=job['state'])

    return router
