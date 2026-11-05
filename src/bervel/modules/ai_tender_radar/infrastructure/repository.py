from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, Numeric, String, Table, case, cast as sql_cast, func, literal_column, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH, UUID as UUIDColumn, insert
from sqlalchemy.engine import Connection, Engine

from bervel.core.errors import CoreError
from bervel.core.infrastructure.database import metadata
from bervel.core.models import ActorContext, ArtifactRef
from bervel.modules.ai_tender_radar.models import AnalysisResult, ArchivedTender, CardPage, CardSort, ClaimedJob, JobView, StockSummary, TenderCard, TenderData, TenderSummary


jobs = Table('ai_tender_radar_jobs', metadata,
    Column('job_id', UUIDColumn(as_uuid=True), primary_key=True),
    Column('batch_id', UUIDColumn(as_uuid=True)),
    Column('data_scope', String(128), nullable=False),
    Column('actor_id', String(128), nullable=False),
    Column('command_key', String(128), nullable=False),
    Column('stock_artifact_id', UUIDColumn(as_uuid=True), nullable=False),
    Column('stock_sha256', String(64), nullable=False),
    Column('search_phrase', String(500), nullable=False),
    Column('state', String(16), nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('updated_at', DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column('lease_until', DateTime(timezone=True)),
    Column('kontur_id', String(128)),
    Column('target_kontur_id', String(128)),
    Column('stock', JSONB(none_as_null=True)),
    Column('tender', JSONB(none_as_null=True)),
    Column('snapshot', JSONB(none_as_null=True)),
    Column('analysis', JSONB(none_as_null=True)),
    Column('error', JSONB(none_as_null=True)),
)
VIEW = tuple(jobs.c[name] for name in JobView.__annotations__)
CLAIM = tuple(jobs.c[name] for name in ClaimedJob.__annotations__)
ACTIVE = jobs.c.state.in_(('queued', 'running'))
LEASE = literal_column("interval '5 minutes'")
ARCHIVE_PHRASE = 'JSON'


class PostgresAiTenderRadarRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _existing(conn: Connection, actor: ActorContext, key: str, ref: ArtifactRef,
                  phrase: str, target: str | None, archive_id: str | None = None) -> JobView | None:
        source = jobs.c.snapshot['source']['artifact_id'].astext
        row = conn.execute(select(*VIEW, jobs.c.target_kontur_id, source.label('archive_id')).where(jobs.c.data_scope == actor.data_scope,
            jobs.c.actor_id == actor.actor_id, jobs.c.command_key == key)).mappings().one_or_none()
        if row is None:
            return None
        if (row['stock_artifact_id'] != ref.artifact_id or row['search_phrase'] != phrase
                or row['target_kontur_id'] != target or row['archive_id'] != archive_id):
            raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ команды уже использован с другими источниками или поисковой фразой.')
        return cast(JobView, {name: row[name] for name in JobView.__annotations__})

    def replay(self, actor: ActorContext, key: str, ref: ArtifactRef, phrase: str, target: str | None,
               archive_id: str | None = None) -> JobView | None:
        with self.engine.connect() as conn:
            return self._existing(conn, actor, key, ref, phrase, target, archive_id)

    def enqueue(self, actor: ActorContext, key: str, ref: ArtifactRef, phrase: str, sha256: str,
                target: str | None, snapshot: ArchivedTender | None = None) -> JobView:
        statement = insert(jobs).values(job_id=uuid4(), data_scope=actor.data_scope, actor_id=actor.actor_id,
            command_key=key, stock_artifact_id=ref.artifact_id, stock_sha256=sha256,
            search_phrase=phrase, target_kontur_id=target, snapshot=snapshot, state='queued').on_conflict_do_nothing().returning(*VIEW)
        with self.engine.begin() as conn:
            row = conn.execute(statement).mappings().one_or_none()
            if row is not None:
                return cast(JobView, dict(row))
            existing = self._existing(conn, actor, key, ref, phrase, target,
                                      snapshot['source']['artifact_id'] if snapshot is not None else None)
            if existing is not None:
                return existing
            raise CoreError('PROCESS_ACTIVE', 'В этой области доступа уже запущено задание Тендерного радара ИИ.')

    def _existing_archive(self, conn: Connection, actor: ActorContext, key: str, ref: ArtifactRef,
                          archive_id: str) -> tuple[JobView, int] | None:
        job = self._existing(conn, actor, key, ref, ARCHIVE_PHRASE, None, archive_id)
        if job is None:
            return None
        total = conn.execute(select(func.count()).select_from(jobs).where(jobs.c.batch_id == job['batch_id'])).scalar_one()
        return job, total

    def replay_archive(self, actor: ActorContext, key: str, ref: ArtifactRef, archive_id: str) -> tuple[JobView, int] | None:
        with self.engine.connect() as conn:
            return self._existing_archive(conn, actor, key, ref, archive_id)

    def enqueue_archive(self, actor: ActorContext, key: str, ref: ArtifactRef, sha256: str,
                        snapshots: list[ArchivedTender]) -> tuple[JobView, int]:
        batch = uuid4()
        values = dict(batch_id=batch, data_scope=actor.data_scope, actor_id=actor.actor_id,
                      stock_artifact_id=ref.artifact_id, stock_sha256=sha256, search_phrase=ARCHIVE_PHRASE)
        with self.engine.begin() as conn:
            row = conn.execute(insert(jobs).values(**values, job_id=batch, command_key=key,
                state='queued', snapshot=snapshots[0]).on_conflict_do_nothing().returning(*VIEW)).mappings().one_or_none()
            if row is None:
                existing = self._existing_archive(conn, actor, key, ref, snapshots[0]['source']['artifact_id'])
                if existing is not None:
                    return existing
                raise CoreError('PROCESS_ACTIVE', 'В этой области доступа уже запущено задание Тендерного радара ИИ.')
            if len(snapshots) > 1:
                conn.execute(jobs.insert(), [dict(values, job_id=uuid4(), command_key=f'json-{batch.hex}-{index:04d}',
                    state='waiting', snapshot=snapshot) for index, snapshot in enumerate(snapshots[1:], 1)])
            return cast(JobView, dict(row)), len(snapshots)

    def archive_snapshot(self, scope: str, job_id: UUID) -> ArchivedTender | None:
        with self.engine.connect() as conn:
            snapshot = conn.execute(select(jobs.c.snapshot).where(
                jobs.c.data_scope == scope, jobs.c.job_id == job_id)).scalar_one_or_none()
        return cast(ArchivedTender, snapshot) if snapshot and 'source' in snapshot else None

    def read(self, scope: str, job_id: UUID) -> JobView | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(*VIEW).where(jobs.c.data_scope == scope, jobs.c.job_id == job_id)).mappings().one_or_none()
            return cast(JobView, dict(row)) if row is not None else None

    def active(self, scope: str) -> JobView | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(*VIEW).where(jobs.c.data_scope == scope, ACTIVE)).mappings().one_or_none()
            return cast(JobView, dict(row)) if row is not None else None

    def cards(self, scope: str, limit: int, offset: int, sort: CardSort | None = None) -> CardPage:
        status = jobs.c.analysis['status'].astext
        positions = jobs.c.analysis['positions']
        full_positions = func.jsonb_path_query_array(positions, sql_cast('$[*] ? (@.stock_status == "full")', JSONPATH))
        position_coverage = 100 * sql_cast(func.jsonb_array_length(full_positions), Numeric) / func.nullif(
            func.jsonb_array_length(positions), 0)
        source_positions = jobs.c.snapshot['tender']['positions']
        source_prices = func.jsonb_path_exists(source_positions,
            sql_cast('$[*] ? (@.item_price != null || @.position_price != null)', JSONPATH))
        benefit_reason = case(
            ((func.jsonb_array_length(source_positions) > 0) & ~source_prices, 'TENDER_PRICES_NOT_DISCLOSED'),
            else_=jobs.c.analysis['economics']['reason'].astext)
        latest = select(jobs.c.job_id, jobs.c.stock_artifact_id, jobs.c.updated_at, jobs.c.tender,
            status.label('status'), jobs.c.analysis['coverage_percent'].astext.label('coverage_percent'),
            sql_cast(position_coverage, String).label('position_coverage_percent'),
            jobs.c.analysis['benefit_percent'].astext.label('benefit_percent'),
            benefit_reason.label('benefit_reason'),
            jobs.c.analysis['economics']['scope'].astext.label('benefit_scope'),
            jobs.c.analysis['mock'].as_boolean().label('mock')).where(
            jobs.c.data_scope == scope, jobs.c.state == 'loaded', jobs.c.analysis.is_not(None)
        ).distinct(jobs.c.kontur_id).order_by(jobs.c.kontur_id,
            case((status == 'partial', 1), else_=0), jobs.c.updated_at.desc(), jobs.c.job_id.desc()).subquery()
        page = select(latest)
        if sort is not None:
            metric = (func.coalesce(latest.c.coverage_percent, latest.c.position_coverage_percent)
                      if sort == 'coverage' else latest.c.benefit_percent)
            page = page.order_by(sql_cast(metric, Numeric).desc().nulls_last())
        page = page.order_by(latest.c.updated_at.desc(), latest.c.job_id.desc()).offset(offset).limit(limit + 1)
        with self.engine.connect() as conn:
            rows = conn.execute(page).mappings().all()
        return CardPage(cards=[cast(TenderCard, dict(row)) for row in rows[:limit]],
                        next_offset=offset + limit if len(rows) > limit else None)

    def claim(self) -> ClaimedJob | None:
        with self.engine.begin() as conn:
            expired = conn.execute(jobs.update().where(jobs.c.state == 'running', jobs.c.lease_until <= func.now()).values(
                state='failed', lease_until=None, updated_at=func.now(),
                error={'code': 'AI_RADAR_JOB_INTERRUPTED', 'message': 'Выполнение прервалось. Для новой попытки запустите новое задание.'})
                .returning(jobs.c.batch_id)).scalars().all()
            for batch in expired:
                self._advance(conn, batch)
            next_id = select(jobs.c.job_id).where(jobs.c.state == 'queued').order_by(jobs.c.created_at, jobs.c.job_id
                ).limit(1).with_for_update(skip_locked=True).scalar_subquery()
            row = conn.execute(jobs.update().where(jobs.c.job_id == next_id).values(state='running',
                updated_at=func.now(), lease_until=func.now() + LEASE
                ).returning(*CLAIM)).mappings().one_or_none()
            return cast(ClaimedJob, dict(row)) if row is not None else None

    def heartbeat(self, job_id: UUID) -> None:
        with self.engine.begin() as conn:
            result = conn.execute(jobs.update().where(jobs.c.job_id == job_id, jobs.c.state == 'running',
                jobs.c.lease_until > func.now()).values(lease_until=func.now() + LEASE, updated_at=func.now()))
            if result.rowcount != 1:
                raise CoreError('AI_RADAR_JOB_INTERRUPTED', 'Время владения заданием истекло.')

    def processed_ids(self, scope: str, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        with self.engine.connect() as conn:
            return set(conn.execute(select(jobs.c.kontur_id).where(jobs.c.data_scope == scope,
                jobs.c.state == 'loaded', jobs.c.analysis.is_not(None), jobs.c.kontur_id.in_(ids))).scalars())

    def _finish(self, job_id: UUID, **values) -> None:
        with self.engine.begin() as conn:
            batch = conn.execute(jobs.update().where(jobs.c.job_id == job_id, jobs.c.state == 'running',
                jobs.c.lease_until > func.now()).values(lease_until=None, updated_at=func.now(), **values)
                .returning(jobs.c.batch_id)).scalar_one_or_none()
            self._advance(conn, batch)

    @staticmethod
    def _advance(conn: Connection, batch: UUID | None) -> None:
        if batch is not None:
            next_id = select(jobs.c.job_id).where(jobs.c.batch_id == batch, jobs.c.state == 'waiting') \
                .order_by(jobs.c.command_key).limit(1).scalar_subquery()
            conn.execute(jobs.update().where(jobs.c.job_id == next_id).values(state='queued', updated_at=func.now()))

    def complete(self, job_id: UUID, stock: StockSummary, tender: TenderData | None, analysis: AnalysisResult | None,
                 archived: ArchivedTender | None = None) -> None:
        if tender is None:
            self._finish(job_id, state='no_match', stock=stock)
            return
        details = TenderSummary(id=tender['id'], title=tender['title'], number=tender['number'],
            link=tender['link'], position_count=len(tender['positions']), limitations=tender['limitations'])
        if tender.get('simulated'):
            details['simulated'] = True
        self._finish(job_id, state='loaded', kontur_id=tender['id'], stock=stock, tender=details, analysis=analysis,
            snapshot=archived if archived is not None else {'schema_version': 1, 'tender': tender})

    def fail(self, job_id: UUID, error: CoreError) -> None:
        self._finish(job_id, state='failed', error={'code': error.code, 'message': error.message})
