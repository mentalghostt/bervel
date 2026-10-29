"""Explicit browser DTOs; model execution stays in the worker."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from bervel.core.api.models import ErrorResponse
from bervel.modules.chat.service import ChatService
from bervel.modules.chat.models import WorkflowPhase
from bervel.core.models import ActorContext


class SessionBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    command_key: str = Field(min_length=1, max_length=128)


class MessageBody(SessionBody):
    content: str = Field(min_length=1, max_length=4000)


from bervel.core.api.sources import SourceResponse


class WorkflowHistoryResponse(BaseModel):
    phase: WorkflowPhase
    at: str
    reason: str | None


class WorkflowResponse(BaseModel):
    phase: WorkflowPhase
    updated_at: str
    reason: str | None
    research_rounds: int
    max_research_rounds: int
    web_searches: int
    max_web_searches: int
    elapsed_seconds: float
    max_research_seconds: float
    history: list[WorkflowHistoryResponse]


class TurnResponse(BaseModel):
    turn_id: UUID
    question: str
    answer: str | None
    state: Literal['queued', 'running', 'completed', 'partial', 'blocked', 'failed']
    created_at: datetime
    sources: list[SourceResponse]
    error: str | None
    results: list[dict] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    workflow: WorkflowResponse | None = None


class SessionResponse(BaseModel):
    session_id: UUID
    created_at: datetime
    turns: list[TurnResponse]


class SessionSummaryResponse(BaseModel):
    session_id: UUID
    created_at: datetime
    title: str
    turn_count: int
    has_pending: bool


class SessionListResponse(BaseModel):
    sessions: list[SessionSummaryResponse]
    next_offset: int | None


class DeletedSessionResponse(BaseModel):
    session_id: UUID
    deleted: Literal[True]


def chat_router(service: ChatService, actor, *, prefix: str = '/api/v0.1/chat') -> APIRouter:
    router = APIRouter(prefix=prefix, tags=['Проверка' if prefix.endswith('/inspection') else 'Чат'], dependencies=[Depends(actor)],
        responses={status: {'model': ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)})

    @router.post('/sessions', response_model=SessionResponse)
    def create(body: SessionBody, context: Annotated[ActorContext, Depends(actor)]):
        return service.create(context, body.command_key)

    @router.get('/sessions', response_model=SessionListResponse)
    def list_sessions(context: Annotated[ActorContext, Depends(actor)],
                      limit: Annotated[int, Query(ge=1, le=100)] = 50,
                      offset: Annotated[int, Query(ge=0)] = 0):
        return service.list_sessions(context, limit=limit, offset=offset)

    @router.get('/sessions/{session_id}', response_model=SessionResponse)
    def read(session_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        return service.read(context, session_id)

    @router.delete('/sessions/{session_id}', response_model=DeletedSessionResponse)
    def delete(session_id: UUID, command_key: Annotated[str, Query(min_length=1, max_length=128)],
               context: Annotated[ActorContext, Depends(actor)]):
        return service.delete(context, session_id, command_key)

    @router.post('/sessions/{session_id}/messages', status_code=202, response_model=TurnResponse)
    def send(session_id: UUID, body: MessageBody, context: Annotated[ActorContext, Depends(actor)]):
        return service.send(context, session_id, body.command_key, body.content)

    return router
