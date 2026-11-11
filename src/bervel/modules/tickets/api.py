"""Explicit ticket DTOs; sending is absent from the HTTP surface."""
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from bervel.core.api.models import ErrorResponse
from bervel.core.models import ActorContext
from bervel.modules.tickets import TicketService


class TicketContent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=200)
    problem: str = Field(min_length=1, max_length=8000)
    expected: str = Field(default='', max_length=8000)
    actual: str = Field(default='', max_length=8000)
    steps: list[str] = Field(default_factory=list, max_length=50)
    context: str = Field(default='', max_length=8000)
    links: list[str] = Field(default_factory=list, max_length=30)


class TicketBody(TicketContent):
    command_key: str = Field(min_length=1, max_length=128)


class TicketEdit(TicketBody):
    expected_revision: int = Field(ge=1)


class TicketResponse(TicketContent):
    ticket_id: UUID
    status: Literal['created', 'sent']
    revision: int
    created_at: datetime
    updated_at: datetime


class TicketList(BaseModel):
    tickets: list[TicketResponse]
    next_cursor: int | None


def tickets_router(service: TicketService, actor) -> APIRouter:
    router = APIRouter(prefix='/api/v0.1/tickets', tags=['Обращения'],
        responses={status: {'model': ErrorResponse} for status in (401, 403, 404, 409, 422, 500, 503)})

    @router.post('', status_code=201, response_model=TicketResponse)
    def create(body: TicketBody, context: Annotated[ActorContext, Depends(actor)]):
        return service.create(context, body.model_dump(exclude={'command_key'}), body.command_key)

    @router.get('', response_model=TicketList)
    def list_tickets(context: Annotated[ActorContext, Depends(actor)], cursor: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return service.list(context, cursor, limit)

    @router.get('/{ticket_id}', response_model=TicketResponse)
    def read(ticket_id: UUID, context: Annotated[ActorContext, Depends(actor)]):
        return service.read(context, ticket_id)

    @router.put('/{ticket_id}', response_model=TicketResponse)
    def edit(ticket_id: UUID, body: TicketEdit, context: Annotated[ActorContext, Depends(actor)]):
        return service.edit(context, ticket_id, body.model_dump(exclude={'command_key', 'expected_revision'}), body.expected_revision, body.command_key)

    return router
