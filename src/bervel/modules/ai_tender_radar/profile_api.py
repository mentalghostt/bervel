"""HTTP boundary for the two fixed, scope-owned radar profiles."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from bervel.core.models import ActorContext

Source = Literal['test_api', 'kontur_api']


class ProfileKeywordsBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    keyword_mode: Literal['auto', 'manual']
    keywords: list[str]


class ProfileScheduleBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool
    every_days: int = Field(ge=1)
    time: str = Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    timezone: Literal['Europe/Moscow']


class ProfileRefreshBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    command_key: str = Field(min_length=1, max_length=128)
    detail_max: int | None = Field(ge=0, le=2_147_483_647)
    schedule: ProfileScheduleBody | None = None
    reanalyze_existing: bool = False


class ProfileScheduleSaveBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    command_key: str = Field(min_length=1, max_length=128)
    schedule: ProfileScheduleBody
    reanalyze_existing: bool


class ProfileResumeBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    command_key: str = Field(min_length=1, max_length=128)
    rating_version: Literal['card-individual-rating-v1'] | None = None


class ProfileMetricsBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    command_key: str = Field(min_length=1, max_length=128)


def profiles_router(service, actor):
    router = APIRouter(prefix='/api/v0.1/ai-tender-radar/profiles')

    @router.get('')
    def listing(context: Annotated[ActorContext, Depends(actor)]):
        return service.list(context)

    @router.get('/{source}')
    def read(source: Source, context: Annotated[ActorContext, Depends(actor)]):
        # Prepare the large response in this sync endpoint's worker thread;
        # returning a dict would run FastAPI's recursive encoding on the event loop.
        return JSONResponse(jsonable_encoder(service.read(context, source)))

    @router.put('/{source}/keywords')
    def keywords(source: Source, body: ProfileKeywordsBody, context: Annotated[ActorContext, Depends(actor)]):
        return service.save_keywords(context, source, body.keyword_mode, body.keywords)

    @router.post('/{source}/refresh', status_code=202)
    def refresh(source: Source, body: ProfileRefreshBody, context: Annotated[ActorContext, Depends(actor)]):
        job = service.refresh(context, source, body.command_key, body.detail_max,
            body.schedule.model_dump() if body.schedule is not None else None,
            **({'reanalyze_existing': body.reanalyze_existing} if 'reanalyze_existing' in body.model_fields_set else {}))
        return dict(job_id=str(job['job_id']), run_id=str(job['run_id']), state=job['state'])

    @router.post('/{source}/schedule')
    def schedule(source: Source, body: ProfileScheduleSaveBody, context: Annotated[ActorContext, Depends(actor)]):
        return service.save_schedule(context, source, body.command_key, body.schedule.model_dump(), body.reanalyze_existing)

    @router.post('/{source}/resume', status_code=202)
    def resume(source: Source, body: ProfileResumeBody, context: Annotated[ActorContext, Depends(actor)]):
        job = service.resume(context, source, body.command_key,
            **({'rating_version': body.rating_version} if body.rating_version is not None else {}))
        return dict(job_id=str(job['job_id']), run_id=str(job['run_id']), state=job['state'])

    @router.post('/{source}/metrics', status_code=202)
    def metrics(source: Source, body: ProfileMetricsBody, context: Annotated[ActorContext, Depends(actor)]):
        job = service.metrics(context, source, body.command_key)
        return dict(job_id=str(job['job_id']), run_id=str(job['run_id']), state=job['state'])

    return router
