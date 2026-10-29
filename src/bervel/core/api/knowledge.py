"""Public indexing state and explicit recovery; no credentials or vectors."""
from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Path
from pydantic import BaseModel,ConfigDict

from bervel.core.models import ActorContext,ArtifactRef


class IndexStatus(BaseModel):
    state:Literal['pending','running','ready','partial','unsupported','failed']
    error:str|None
    total_chunks:int
    completed_chunks:int
    embedding_model:str|None=None


class ResumeBody(BaseModel):
    model_config=ConfigDict(extra='forbid')


def knowledge_router(service,actor):
    router=APIRouter(prefix='/api/v0.1/artifacts',tags=['База знаний'])
    @router.get('/{artifact_id}/knowledge',response_model=IndexStatus)
    def status(artifact_id:UUID,context:Annotated[ActorContext,Depends(actor)]):
        return service.status(context,ArtifactRef(artifact_id))
    @router.post('/{artifact_id}/knowledge/resume',response_model=IndexStatus)
    def resume(artifact_id:UUID,body:ResumeBody,context:Annotated[ActorContext,Depends(actor)]):
        service.resume(context,ArtifactRef(artifact_id))
        return service.status(context,ArtifactRef(artifact_id))
    return router
