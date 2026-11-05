"""Explicit HTTP confirmation is deliberately absent from model operation tools."""
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from bervel.core.models import ActorContext


class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command_key: str = Field(min_length=1, max_length=128)
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')


def engineering_router(service, actor):
    router = APIRouter(prefix='/api/v0.1/inspection/proposals', tags=['Проверка'])
    @router.get('/{proposal_id}')
    def read(proposal_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        return service.read(context, proposal_id)
    @router.post('/{proposal_id}/confirm')
    def confirm(proposal_id: UUID, body: ProposalDecision, context: Annotated[ActorContext, Depends(actor)]):
        return service.confirm(context, proposal_id, body.command_key, body.fingerprint)
    @router.post('/{proposal_id}/reject')
    def reject(proposal_id: UUID, body: ProposalDecision, context: Annotated[ActorContext, Depends(actor)]):
        return service.reject(context, proposal_id, body.command_key, body.fingerprint)
    return router
