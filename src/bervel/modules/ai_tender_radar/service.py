from collections.abc import Callable
from datetime import datetime
from pathlib import PurePath
import re
from typing import Protocol
from uuid import UUID

from bervel.core.csv_memory import MAX_BYTES
from bervel.core.errors import CoreError
from bervel.core.model_gateway import ModelClient
from bervel.core.models import ActorContext, ArtifactRef
from bervel.core.services import ArtifactService, authorize, validate_key
from bervel.modules.ai_tender_radar.calculation import calculate_analysis
from bervel.modules.ai_tender_radar.matching import match_positions
from bervel.modules.ai_tender_radar.models import AnalysisResult, ArchivedTender, ArchiveSource, CardPage, CardSort, ClaimedJob, JobView, StockData, StockSummary, TenderData


MAX_ARCHIVE_BYTES = 16 * 1024 * 1024


class JobRepository(Protocol):
    def replay(self, actor: ActorContext, key: str, ref: ArtifactRef, phrase: str, target: str | None,
               archive_id: str | None = None) -> JobView | None: ...
    def enqueue(self, actor: ActorContext, key: str, ref: ArtifactRef, phrase: str, sha256: str, target: str | None,
                snapshot: ArchivedTender | None = None) -> JobView: ...
    def replay_archive(self, actor: ActorContext, key: str, ref: ArtifactRef, archive_id: str) -> tuple[JobView, int] | None: ...
    def enqueue_archive(self, actor: ActorContext, key: str, ref: ArtifactRef, sha256: str,
                        snapshots: list[ArchivedTender]) -> tuple[JobView, int]: ...
    def archive_snapshot(self, scope: str, job_id: UUID) -> ArchivedTender | None: ...
    def read(self, scope: str, job_id: UUID) -> JobView | None: ...
    def active(self, scope: str) -> JobView | None: ...
    def cards(self, scope: str, limit: int, offset: int, sort: CardSort | None = None) -> CardPage: ...
    def claim(self) -> ClaimedJob | None: ...
    def heartbeat(self, job_id: UUID) -> None: ...
    def processed_ids(self, scope: str, ids: list[str]) -> set[str]: ...
    def complete(self, job_id: UUID, stock: StockSummary, tender: TenderData | None, analysis: AnalysisResult | None,
                 archived: ArchivedTender | None = None) -> None: ...
    def fail(self, job_id: UUID, error: CoreError) -> None: ...


class TenderSource(Protocol):
    def fetch_next(self, phrase: str, processed_ids: Callable[[list[str]], set[str]]) -> TenderData | None: ...
    def fetch_tender(self, key: str) -> TenderData: ...


class AiTenderRadar:
    def __init__(self, jobs: JobRepository, artifacts: ArtifactService, kontur: TenderSource,
                 parse_stock: Callable[[bytes], StockData], model: ModelClient, parse_archive: Callable[[bytes], list[TenderData]]):
        self.jobs = jobs
        self.artifacts = artifacts
        self.kontur = kontur
        self.parse_stock = parse_stock
        self.model = model
        self.parse_archive = parse_archive

    @staticmethod
    def _check_stock(metadata: dict) -> None:
        if metadata['usage_role'] != 'stock' or PurePath(metadata['filename']).suffix.casefold() != '.csv':
            raise CoreError('VALIDATION_ERROR', 'Выберите CSV с назначением «Склад».')
        if metadata['size'] > MAX_BYTES:
            raise CoreError('VALIDATION_ERROR', 'Складской CSV превышает допустимый размер.')

    def enqueue(self, actor: ActorContext, ref: ArtifactRef, search_phrase: str, command_key: str) -> JobView:
        return self._enqueue(actor, ref, search_phrase, command_key, None)

    def enqueue_archive(self, actor: ActorContext, ref: ArtifactRef, source_ref: ArtifactRef,
                        command_key: str) -> tuple[JobView, int]:
        authorize(actor, 'process:start')
        authorize(actor, 'artifact:read')
        validate_key(command_key)
        archive_id = str(source_ref.artifact_id)
        existing = self.jobs.replay_archive(actor, command_key, ref, archive_id)
        if existing is not None:
            return existing
        stock = self.artifacts.metadata(actor, ref)
        self._check_stock(stock)
        metadata = self.artifacts.metadata(actor, source_ref)
        if PurePath(metadata['filename']).suffix.casefold() != '.json' or metadata['size'] > MAX_ARCHIVE_BYTES:
            raise CoreError('VALIDATION_ERROR', 'Выберите файл JSON размером до 16 МиБ.')
        artifact = self.artifacts.read(actor, source_ref)
        source = ArchiveSource(artifact_id=archive_id, filename=metadata['filename'], sha256=metadata['sha256'])
        snapshots = [ArchivedTender(schema_version=1, tender=tender, source=source)
                     for tender in self.parse_archive(artifact.content)]
        return self.jobs.enqueue_archive(actor, command_key, ref, stock['sha256'], snapshots)

    def reanalyze(self, actor: ActorContext, job_id: UUID, ref: ArtifactRef, command_key: str) -> JobView:
        authorize(actor, 'process:start')
        previous = self.read(actor, job_id)
        tender = previous['tender']
        if tender is None or previous['analysis'] is None:
            raise CoreError('NOT_FOUND', 'Карточка для повторного анализа не найдена.')
        snapshot = self.jobs.archive_snapshot(actor.data_scope, job_id)
        if snapshot is None and bool(tender.get('simulated')) != bool(getattr(self.kontur, 'simulated', False)):
            raise CoreError('VALIDATION_ERROR', 'Для повторного анализа включите тот же источник закупок, что использовался в карточке.')
        return self._enqueue(actor, ref, previous['search_phrase'], command_key, tender['id'], snapshot)

    def _enqueue(self, actor: ActorContext, ref: ArtifactRef, search_phrase: str,
                 command_key: str, target: str | None, snapshot: ArchivedTender | None = None) -> JobView:
        authorize(actor, 'process:start')
        authorize(actor, 'artifact:read')
        validate_key(command_key)
        phrase = ' '.join(search_phrase.split())
        if not 1 <= len(phrase) <= 500 or re.search(r'[\x00-\x1f\ud800-\udfff]', phrase + command_key):
            raise CoreError('VALIDATION_ERROR', 'Поисковая фраза должна содержать от 1 до 500 символов. Проверьте также ключ команды.')
        existing = self.jobs.replay(actor, command_key, ref, phrase, target,
                                    snapshot['source']['artifact_id'] if snapshot is not None else None)
        if existing is not None:
            return existing
        metadata = self.artifacts.metadata(actor, ref)
        self._check_stock(metadata)
        return self.jobs.enqueue(actor, command_key, ref, phrase, metadata['sha256'], target, snapshot)

    def read(self, actor: ActorContext, job_id: UUID) -> JobView:
        authorize(actor, 'process:read')
        authorize(actor, 'artifact:read')
        job = self.jobs.read(actor.data_scope, job_id)
        if job is None:
            raise CoreError('NOT_FOUND', 'Задание Тендерного радара ИИ не найдено.')
        return job

    def active(self, actor: ActorContext) -> JobView | None:
        authorize(actor, 'process:read')
        authorize(actor, 'artifact:read')
        return self.jobs.active(actor.data_scope)

    def cards(self, actor: ActorContext, limit: int, offset: int, sort: CardSort | None = None) -> CardPage:
        authorize(actor, 'process:read')
        authorize(actor, 'artifact:read')
        return self.jobs.cards(actor.data_scope, limit, offset, sort)

    def run_once(self) -> bool:
        job = self.jobs.claim()
        if job is None:
            return False
        scope = job['data_scope']
        job_id = job['job_id']
        actor = ActorContext(job['actor_id'], scope, frozenset({'artifact:read'}))
        ref = ArtifactRef(job['stock_artifact_id'])
        archived = job['snapshot']
        try:
            metadata = self.artifacts.metadata(actor, ref)
            self._check_stock(metadata)
            artifact = self.artifacts.read(actor, ref)
            self._check_stock(artifact.metadata)
            if artifact.metadata['sha256'] != job['stock_sha256']:
                raise CoreError('STALE_REVISION', 'Выбранный склад изменился после постановки задания.')
            stock = self.parse_stock(artifact.content)
            dates = [datetime.fromisoformat(row['as_of']) for row in stock['rows'] if row['as_of']]
            summary = StockSummary(filename=artifact.metadata['filename'], sha256=job['stock_sha256'],
                row_count=len(stock['rows']), data_kind=stock['data_kind'],
                as_of_from=min(dates).isoformat() if dates else None, as_of_to=max(dates).isoformat() if dates else None)
            target = job['target_kontur_id']
            if archived is not None:
                tender = archived['tender']
            elif target:
                tender = self.kontur.fetch_tender(target)
            else:
                tender = self.kontur.fetch_next(job['search_phrase'], lambda ids: self.jobs.processed_ids(scope, ids))
            analysis = None
            if tender is not None:
                matching = match_positions(tender, stock, self.model, f'ai-tender-radar-{job_id}',
                                           lambda: self.jobs.heartbeat(job_id))
                self.jobs.heartbeat(job_id)
                analysis = calculate_analysis(tender, stock, matching)
                if tender.get('simulated'):
                    analysis['mock'] = True
        except CoreError as error:
            self.jobs.fail(job_id, error)
        else:
            self.jobs.complete(job_id, summary, tender, analysis, archived)
        return True
