"""Preview transport reuses the original's access and integrity checks."""
from typing import Annotated,Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Query,Path
from pydantic import BaseModel
from bervel.core.models import ActorContext,ArtifactRef
from bervel.core.infrastructure.file_preview import preview


class PreviewResponse(BaseModel):
    kind:Literal['table','text','pdf','image','unsupported']
    media_type:str
    columns:list[str]
    rows:list[list[str]]
    total_rows:int
    next_cursor:int|None
    text:str
    notice:str


def preview_router(artifacts,actor):
    router=APIRouter(prefix='/api/v0.1/artifacts',tags=['Просмотр файлов'])
    @router.get('/{artifact_id}/preview',response_model=PreviewResponse)
    def read(artifact_id:UUID,context:Annotated[ActorContext,Depends(actor)],
             cursor:Annotated[int,Query(ge=0)]=0,limit:Annotated[int,Query(ge=1,le=100)]=100):
        original=artifacts.read(context,ArtifactRef(artifact_id))
        return preview(original.content,original.metadata['filename'],original.metadata['media_type'],cursor=cursor,limit=limit)
    return router
