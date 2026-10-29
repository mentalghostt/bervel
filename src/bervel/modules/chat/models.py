"""Public results of chat operations; persisted timestamps are ISO 8601 strings."""
from typing import Literal, TypedDict

from bervel.core.document_models import DocumentSource


WorkflowPhase = Literal['research', 'synthesizing', 'generating', 'verifying', 'completed', 'partial', 'blocked']


class WorkflowHistory(TypedDict):
    phase: WorkflowPhase
    at: str
    reason: str | None


class WorkflowSummary(TypedDict):
    phase: WorkflowPhase
    updated_at: str
    reason: str | None
    research_rounds: int
    max_research_rounds: int
    web_searches: int
    max_web_searches: int
    elapsed_seconds: float
    max_research_seconds: float
    history: list[WorkflowHistory]


class ChatTurn(TypedDict):
    turn_id: str
    question: str
    answer: str | None
    state: Literal['queued', 'running', 'completed', 'partial', 'blocked', 'failed']
    created_at: str
    sources: list[DocumentSource]
    error: str | None
    results: list[dict]
    actions: list[dict]
    workflow: WorkflowSummary | None


class ChatSession(TypedDict):
    session_id: str
    created_at: str
    turns: list[ChatTurn]


class ChatSessionSummary(TypedDict):
    session_id: str
    created_at: str
    title: str
    turn_count: int
    has_pending: bool


class ChatSessionList(TypedDict):
    sessions: list[ChatSessionSummary]
    next_offset: int | None


class DeletedChatSession(TypedDict):
    session_id: str
    deleted: Literal[True]
