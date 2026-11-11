"""Composition of customer-request services and their dedicated worker."""
from pathlib import Path

from bervel.core.infrastructure.codex import CodexClient
from bervel.core.infrastructure.database import make_engine
from bervel.core.infrastructure.repository import PostgresRepository
from bervel.core.infrastructure.storage import FileObjectStore
from bervel.core.services import ArtifactService
from bervel.modules.company_settings import CompanySettings
from bervel.modules.company_settings.infrastructure.repository import PostgresCompanySettingsRepository
from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
from bervel.modules.presales import PresalesService
from bervel.modules.presales.infrastructure.documents import OfferRenderer
from bervel.modules.presales.infrastructure.repository import PostgresPresalesRepository
from bervel.modules.presales.infrastructure.source_reader import RequestSourceReader


def build_presales(engine, artifacts, company, model, *, workspace: Path | None = None):
    processing = workspace if workspace is not None else artifacts.objects.root / 'presales-processing'
    processing.mkdir(parents=True, exist_ok=True)
    return PresalesService(PostgresPresalesRepository(engine), artifacts, company,
        reader=RequestSourceReader(workspace=processing), model=model, renderer=OfferRenderer(workspace=processing))


def build_presales_worker(settings):
    engine = make_engine(settings.database_url)
    try:
        artifacts = ArtifactService(PostgresRepository(engine), FileObjectStore(settings.object_root))
        company = CompanySettings(PostgresCompanySettingsRepository(engine), artifacts, read_stock)
        return engine, build_presales(engine, artifacts, company, CodexClient(settings.codex_url))
    except Exception:
        engine.dispose()
        raise
