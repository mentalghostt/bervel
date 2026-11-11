from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from bervel.core.models import ActorContext, ArtifactRef
from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar import AiTenderRadar
from bervel.modules.ai_tender_radar.models import CardPage, CardSort, JobState, JobView
from bervel.modules.ai_tender_radar.source_contract import PurchaseStage


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


class RunBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    settings_snapshot_id: str
    command_key: str = Field(min_length=1, max_length=128)
    name: str | None = Field(default=None,max_length=200)
    source: Literal['test_api','kontur_api','file'] | None = None
    source_artifact_id: str | None = None
    search_max: int | None = Field(default=None, ge=1)
    detail_max: int | None = Field(default=None, strict=True, ge=1, le=2_147_483_647)
    model_max: int | None = Field(default=None, ge=0)
    purchase_stages: list[PurchaseStage] | None = Field(default=None, min_length=1, max_length=5)


class OperationBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    command_key: str = Field(min_length=1, max_length=128)
    analysis_id: str | None = None
    settings_snapshot_id: str | None = None
    question: str | None = Field(default=None, max_length=2000)
    reference: dict | None = None
    max_cards: int | None = Field(default=None, ge=1)


def runs_router(service, actor):
    router = APIRouter(prefix='/api/v0.1/ai-tender-radar/runs')

    @router.post('', status_code=202)
    def enqueue(body: RunBody, context: Annotated[ActorContext, Depends(actor)]):
        try:
            snapshot_id = UUID(body.settings_snapshot_id)
        except ValueError:
            raise CoreError('VALIDATION_ERROR', 'Некорректный ID снимка настроек.') from None
        try:
            source_artifact_id = UUID(body.source_artifact_id) if body.source_artifact_id is not None else None
        except ValueError:
            raise CoreError('VALIDATION_ERROR', 'Некорректный ID файла закупок.') from None
        modern=bool({'name','source','source_artifact_id','purchase_stages'} & body.model_fields_set)
        if modern and (not body.name or not body.name.strip() or body.source is None):
            raise CoreError('VALIDATION_ERROR','Введите название прогона и выберите источник.')
        job = service.enqueue(context, snapshot_id, body.command_key,
            search_max=body.search_max if modern else body.search_max if body.search_max is not None else 40,
            detail_max=body.detail_max if modern else body.detail_max if body.detail_max is not None else 200,
            model_max=body.model_max if modern else body.model_max if body.model_max is not None else 1000,
            **({'purchase_stages': body.purchase_stages} if body.purchase_stages is not None else {}),
            **({'name':body.name,'source':body.source,'source_artifact_id':source_artifact_id} if modern else {}))
        return {'run_id': str(job['job_id']), 'job_id': str(job['job_id']), 'state': job['state']}

    @router.get('')
    def listing(context: Annotated[ActorContext, Depends(actor)], limit: Annotated[int, Query(ge=1, le=50)] = 30,
                offset: Annotated[int, Query(ge=0)] = 0):
        return service.list(context, limit, offset)

    @router.get('/history')
    def history(context: Annotated[ActorContext, Depends(actor)],limit: Annotated[int,Query(ge=1,le=50)]=30,
                offset: Annotated[int,Query(ge=0)]=0):
        return service.history(context,limit,offset)

    @router.get('/legacy')
    def legacy(context: Annotated[ActorContext, Depends(actor)],limit: Annotated[int,Query(ge=1,le=50)]=30,
               offset: Annotated[int,Query(ge=0)]=0):
        return service.legacy(context,limit,offset)

    @router.delete('/legacy', status_code=204, response_class=Response)
    def delete_legacy(context: Annotated[ActorContext, Depends(actor)]) -> Response:
        service.delete_legacy(context)
        return Response(status_code=204)

    @router.get('/{run_id}')
    def read(run_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        return service.read(context, run_id)

    @router.delete('/{run_id}', status_code=204, response_class=Response)
    def delete(run_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        service.delete(context, run_id)
        return Response(status_code=204)

    @router.post('/{run_id}/stop', status_code=204, response_class=Response)
    def stop(run_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        service.stop(context, run_id)
        return Response(status_code=204)

    @router.post('/{run_id}/{kind}', status_code=202)
    def operation(run_id: UUID, kind: str, body: OperationBody, context: Annotated[ActorContext, Depends(actor)]):
        job = service.operation(context, run_id, body.command_key, kind, analysis_id=body.analysis_id,
            question=body.question, reference=body.reference, max_cards=body.max_cards,
            settings_snapshot_id=body.settings_snapshot_id)
        return {'job_id': str(job['job_id']), 'state': job['state'],
                **({'run_id': str(job['job_id'])} if kind == 'reanalyze' else {})}

    return router
