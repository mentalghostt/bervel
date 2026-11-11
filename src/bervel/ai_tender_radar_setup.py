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
from bervel.modules.ai_tender_radar.infrastructure.kontur import BASE_URL, SIMULATOR_KEY, KonturClient, KonturRequestGate
from bervel.modules.ai_tender_radar.infrastructure.kontur_credentials import KonturCredentials
from bervel.modules.ai_tender_radar.infrastructure.repository import PostgresAiTenderRadarRepository
from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
from bervel.modules.company_settings import CompanySettings
from bervel.modules.company_settings.infrastructure.repository import PostgresCompanySettingsRepository
from bervel.modules.ai_tender_radar.infrastructure.runs import PostgresRunRepository
from bervel.modules.ai_tender_radar.runs import RadarRuns
from bervel.modules.ai_tender_radar.profiles import RadarProfiles
from bervel.modules.ai_tender_radar.infrastructure.profiles import PostgresProfileRepository


def build_ai_tender_radar(engine: Engine, artifacts: ArtifactService, kontur_key: str, model: ModelClient,
                          *, kontur_base_url: str = BASE_URL, credentials: KonturCredentials | None = None,
                          company: CompanySettings | None = None) -> AiTenderRadar:
    real_gate = KonturRequestGate()
    configured = KonturClient(kontur_key if kontur_key != SIMULATOR_KEY else '', allow_configured_api=True,
                              key_provider=credentials.read if credentials is not None else None, request_gate=real_gate)
    simulated = kontur_base_url.rstrip('/') != BASE_URL
    if simulated:
        kontur = KonturClient(SIMULATOR_KEY, base_url=kontur_base_url)
    else:
        kontur = KonturClient(kontur_key, key_provider=credentials.read if credentials is not None else None,
                             request_gate=real_gate)
    jobs = PostgresAiTenderRadarRepository(engine)
    service = AiTenderRadar(jobs, artifacts, kontur, read_stock, model, read_archive)
    company = company if company is not None else CompanySettings(PostgresCompanySettingsRepository(engine), artifacts, read_stock)
    sources={'test_api':kontur} if kontur.simulated else {}
    if credentials is not None or configured.key:
        sources['kontur_api'] = configured
    service.run_pipeline = RadarRuns(PostgresRunRepository(engine), jobs, company, artifacts, read_stock, kontur, model,
                                    parse_archive=service.parse_archive, sources=sources or None)
    service.run_pipeline.profiles = RadarProfiles(service.run_pipeline, PostgresProfileRepository(engine))
    return service


def build_ai_tender_radar_worker(settings: Settings) -> tuple[Engine, AiTenderRadar]:
    engine = make_engine(settings.database_url)
    try:
        artifacts = ArtifactService(PostgresRepository(engine), FileObjectStore(settings.object_root))
        return engine, build_ai_tender_radar(engine, artifacts, settings.kontur_api_key, CodexClient(settings.codex_url),
                                            kontur_base_url=settings.ai_radar_kontur_base_url,
                                            credentials=KonturCredentials(settings.object_root, settings.kontur_api_key))
    except Exception:
        engine.dispose()
        raise
