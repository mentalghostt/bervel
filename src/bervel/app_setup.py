"""Explicit composition of the platform and the modules shipped in this application."""
from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy.engine import Engine

from bervel.ai_tender_radar_setup import build_ai_tender_radar
from bervel.core.api.app import create_platform_app
from bervel.core.api.preview import preview_router
from bervel.core.api.provider import provider_router
from bervel.core.api.knowledge import knowledge_router
from bervel.core.config import Settings
from bervel.core.document_memory import DocumentMemory
from bervel.core.infrastructure.codex import CodexClient
from bervel.core.infrastructure.database import make_engine
from bervel.core.infrastructure.document_reader import DocumentReader
from bervel.core.infrastructure.knowledge import PostgresKnowledgeRepository
from bervel.core.infrastructure.embeddings import OpenAIEmbeddings
from bervel.core.infrastructure.local_embeddings import LocalEmbeddings
from bervel.core.knowledge import KnowledgeService
from bervel.core.infrastructure.repository import PostgresRepository
from bervel.core.infrastructure.storage import FileObjectStore
from bervel.core.services import ArtifactService
from bervel.modules.ai_tender_radar import AiTenderRadar
from bervel.modules.ai_tender_radar.api import ai_tender_radar_router
from bervel.modules.chat import ChatService, AssistantActions
from bervel.modules.chat.api import chat_router
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from bervel.modules.chat.infrastructure.actions import PostgresActionReceipts
from bervel.modules.chat.infrastructure.documents import SafeDocumentRenderer
from bervel.modules.tickets import TicketService
from bervel.modules.tickets.api import tickets_router
from bervel.modules.tickets.infrastructure.repository import PostgresTicketRepository
from bervel.core.api.voice import voice_router
from bervel.core.infrastructure.voice import VoiceClient
from bervel.modules.inspection.infrastructure.client import InspectionClient
from bervel.modules.inspection.native import NativeInspectionWorkflow
from bervel.modules.inspection.infrastructure.cli import NativeCliClient


@dataclass(frozen=True)
class ApplicationServices:
    engine: Engine
    artifacts: ArtifactService
    chat: ChatService
    ai_tender_radar: AiTenderRadar
    model: CodexClient
    knowledge: KnowledgeService
    tickets: TicketService
    inspection: ChatService


def build_services(settings: Settings, *, embedding_token_budget: int = 8000) -> ApplicationServices:
    engine = make_engine(settings.database_url)
    try:
        repository = PostgresRepository(engine)
        artifacts = ArtifactService(repository, FileObjectStore(settings.object_root))
        embeddings=OpenAIEmbeddings(settings.embedding_api_key,token_budget=embedding_token_budget) if settings.embedding_provider=='openai' else LocalEmbeddings(settings.embedding_cache)
        documents = PostgresKnowledgeRepository(engine,model=embeddings.space,dimensions=embeddings.dimensions)
        reader=DocumentReader()
        knowledge=KnowledgeService(documents,artifacts,reader,embeddings)
        memory = DocumentMemory(artifacts, documents, reader,knowledge=knowledge)
        model = CodexClient(settings.codex_url)
        tickets = TicketService(PostgresTicketRepository(engine))
        actions = AssistantActions(artifacts=artifacts, tickets=tickets, settings=model,
                                   renderer=SafeDocumentRenderer(), receipts=PostgresActionReceipts(engine),knowledge=knowledge)
        chat = ChatService(PostgresChatRepository(engine, documents), memory, model, actions=actions)
        reader = InspectionClient(settings.inspection_url, settings.inspection_token)
        inspection_repository = PostgresChatRepository(engine, documents, mode='inspection')
        def token_for_actor(context):
            return next((token for token, configured in settings.access_keys.items() if configured == context), None)
        inspection = ChatService(inspection_repository, memory=None, model=None, actions=None,
            workflow=NativeInspectionWorkflow(NativeCliClient(settings.codex_url, settings.inspection_token, token_for_actor), artifacts),
            content_filter=reader.redact)
        ai_tender_radar = build_ai_tender_radar(engine, artifacts, settings.kontur_api_key, model,
                                               kontur_base_url=settings.ai_radar_kontur_base_url)
        return ApplicationServices(engine=engine,artifacts=artifacts,chat=chat,ai_tender_radar=ai_tender_radar,
                                   model=model,knowledge=knowledge,tickets=tickets,inspection=inspection)
    except Exception:
        engine.dispose()
        raise


def create_app(settings: Settings) -> FastAPI:
    services = build_services(settings)
    app, actor = create_platform_app(settings, services.engine, services.artifacts)
    app.include_router(preview_router(services.artifacts, actor))
    app.include_router(chat_router(services.chat, actor))
    app.include_router(chat_router(services.inspection, actor, prefix='/api/v0.1/inspection'))
    app.include_router(provider_router(services.model, actor))
    app.include_router(knowledge_router(services.knowledge,actor))
    app.include_router(ai_tender_radar_router(services.ai_tender_radar, actor))
    app.include_router(tickets_router(services.tickets, actor))
    app.include_router(voice_router(VoiceClient(settings.codex_url), actor))
    return app
