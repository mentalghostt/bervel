"""Composition shared by the API and the dedicated AI tender worker."""
from sqlalchemy.engine import Engine

from bervel.core.config import Settings
from bervel.core.infrastructure.codex import CodexClient
from bervel.core.infrastructure.database import make_engine
from bervel.core.infrastructure.repository import PostgresRepository
from bervel.core.infrastructure.storage import FileObjectStore
from bervel.core.model_gateway import ModelClient
from bervel.core.services import ArtifactService
from bervel.modules.ai_tender_radar import AiTenderRadar
from bervel.modules.ai_tender_radar.infrastructure.archive import read_archive
from bervel.modules.ai_tender_radar.infrastructure.kontur import BASE_URL, SIMULATOR_KEY, KonturClient
from bervel.modules.ai_tender_radar.infrastructure.repository import PostgresAiTenderRadarRepository
from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock


def build_ai_tender_radar(engine: Engine, artifacts: ArtifactService, kontur_key: str, model: ModelClient,
                          *, kontur_base_url: str = BASE_URL) -> AiTenderRadar:
    key = SIMULATOR_KEY if kontur_base_url.rstrip('/') != BASE_URL else kontur_key
    kontur = KonturClient(key, base_url=kontur_base_url)
    return AiTenderRadar(PostgresAiTenderRadarRepository(engine), artifacts, kontur, read_stock, model, read_archive)


def build_ai_tender_radar_worker(settings: Settings) -> tuple[Engine, AiTenderRadar]:
    engine = make_engine(settings.database_url)
    try:
        artifacts = ArtifactService(PostgresRepository(engine), FileObjectStore(settings.object_root))
        return engine, build_ai_tender_radar(engine, artifacts, settings.kontur_api_key, CodexClient(settings.codex_url),
                                            kontur_base_url=settings.ai_radar_kontur_base_url)
    except Exception:
        engine.dispose()
        raise
