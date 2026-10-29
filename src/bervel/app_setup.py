"""Explicit composition of the platform and the modules shipped in this application."""
from dataclasses import dataclass

from fastapi import FastAPI
from sqlalchemy.engine import Engine

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
from bervel.modules.chat import ChatService, AssistantActions
from bervel.modules.chat.api import chat_router
from bervel.modules.chat.infrastructure.repository import PostgresChatRepository
from bervel.modules.chat.infrastructure.actions import PostgresActionReceipts
from bervel.modules.chat.infrastructure.documents import SafeDocumentRenderer
from bervel.modules.tickets import TicketService
from bervel.modules.tickets.api import tickets_router
from bervel.modules.tickets.infrastructure.repository import PostgresTicketRepository
from bervel.modules.tender_radar import RadarService
from bervel.modules.tender_radar.api import radar_router
from bervel.modules.tender_radar.infrastructure.importer import TenderImporter
from bervel.modules.tender_radar.infrastructure.stock import StockImporter
from bervel.modules.tender_radar.infrastructure.repository import PostgresRadarRepository
from bervel.modules.tender_radar.infrastructure.economics import EconomicsImporter
from bervel.core.api.voice import voice_router
from bervel.core.infrastructure.voice import VoiceClient
from bervel.modules.inspection.actions import InspectionActions
from bervel.modules.inspection.infrastructure.client import InspectionClient
from bervel.modules.inspection.workflow import InspectionWorkflow


@dataclass(frozen=True)
class ApplicationServices:
    engine: Engine
    artifacts: ArtifactService
    chat: ChatService
    radar: RadarService
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
        radar = RadarService(PostgresRadarRepository(engine), artifacts, memory, TenderImporter(), model,
                             stock_importer=StockImporter(), economics_importer=EconomicsImporter())
        tickets = TicketService(PostgresTicketRepository(engine))
        actions = AssistantActions(artifacts=artifacts, radar=radar, tickets=tickets, settings=model,
                                   renderer=SafeDocumentRenderer(), receipts=PostgresActionReceipts(engine),knowledge=knowledge)
        chat = ChatService(PostgresChatRepository(engine, documents), memory, model, actions=actions)
        reader = InspectionClient(settings.inspection_url, settings.inspection_token)
        inspection = ChatService(PostgresChatRepository(engine, documents, mode='inspection'), memory, model,
            actions=InspectionActions(reader=reader, receipts=PostgresActionReceipts(engine), tickets=tickets),
            workflow=InspectionWorkflow, content_filter=reader.redact)
        return ApplicationServices(engine=engine,artifacts=artifacts,chat=chat,radar=radar,
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
    app.include_router(radar_router(services.radar, actor))
    app.include_router(tickets_router(services.tickets, actor))
    app.include_router(voice_router(VoiceClient(settings.codex_url), actor))
    return app
