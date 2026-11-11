"""PostgreSQL transactions for persistent radar profiles and existing queue jobs."""
from datetime import datetime, timezone
from contextlib import nullcontext
from copy import deepcopy
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as UUIDColumn, insert

from bervel.core.errors import CoreError
from bervel.core.models import ArtifactRef
from bervel.core.infrastructure.database import metadata
from bervel.core.infrastructure.commands import CommandReceipts, digest
from bervel.modules.ai_tender_radar.infrastructure.repository import ACTIVE, LANE, jobs, VIEW
from bervel.modules.ai_tender_radar.infrastructure.runs import runs, analyses
from bervel.modules.ai_tender_radar.profiles import default_data


profiles = Table('ai_radar_profiles', metadata,
    Column('data_scope', String(128), primary_key=True),
    Column('source', String(32), primary_key=True),
    Column('run_id', UUIDColumn(as_uuid=True), ForeignKey('ai_radar_runs.run_id'), unique=True),
    Column('data', JSONB, nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False, server_default=func.now()))


class PostgresProfileRepository:
    def __init__(self, engine):
        self.engine = engine

    def execution(self, repository, scope, run_id, job_id, *, owner_field='current_job_id'):
        return ProfileExecutionRepository(self, repository, scope, run_id, job_id, owner_field=owner_field)

    def recoverable(self):
        with self.engine.connect() as conn:
            result = []
            for field, kind in (('current_job_id', 'profile_refresh'), ('last_metrics_job_id', 'profile_metrics')):
                owner = cast(runs.c.data[field].astext, UUIDColumn(as_uuid=True))
                result.extend(dict(row) for row in conn.execute(select(profiles.c.data_scope, profiles.c.source,
                    jobs.c.actor_id, jobs.c.job_id, jobs.c.operation['kind'].astext.label('kind'))
                    .select_from(profiles.join(runs, profiles.c.run_id == runs.c.run_id)
                    .join(jobs, jobs.c.job_id == owner)).where(jobs.c.state == 'failed',
                    jobs.c.operation['kind'].astext == kind,
                    jobs.c.error['code'].astext == 'AI_RADAR_JOB_INTERRUPTED')).mappings())
            return result

    def resume_refresh(self, queue, actor, source, key, *, expected_job_id=None, rating_version=None):
        request = {'source': source, 'resume': True}
        if rating_version is not None:
            if rating_version != 'card-individual-rating-v1' or expected_job_id is not None:
                raise CoreError('VALIDATION_ERROR', 'Недопустимое обновление версии оценки.')
            request['rating_version'] = rating_version
        with self.engine.begin() as conn:
            row = self._lock(conn, actor.data_scope, source)
            replay = self._replay(conn, actor, key, request)
            if replay is not None:
                return dict(replay, _accepted=False)
            if row['run_id'] is None:
                raise CoreError('NOT_FOUND', 'Нет обновления для продолжения.')
            run = conn.execute(select(runs).where(runs.c.data_scope == actor.data_scope,
                runs.c.run_id == row['run_id']).with_for_update()).mappings().one()
            previous = conn.execute(select(jobs).where(jobs.c.job_id == UUID(run['data']['current_job_id']),
                jobs.c.data_scope == actor.data_scope)).mappings().one()
            if expected_job_id is not None and (previous['job_id'] != expected_job_id
                    or (previous['error'] or {}).get('code') != 'AI_RADAR_JOB_INTERRUPTED'):
                raise CoreError('CONFLICT', 'Прерванное исполнение уже заменено другой командой.')
            if previous['state'] != 'failed' or (previous['operation'] or {}).get('kind') != 'profile_refresh':
                raise CoreError('CONFLICT', 'Продолжение доступно только для прерванного обновления профиля.')
            if conn.scalar(select(jobs.c.job_id).where(jobs.c.data_scope == actor.data_scope,
                    ACTIVE, LANE == 'analysis').limit(1)) is not None:
                raise CoreError('PROCESS_ACTIVE', 'В этой области доступа уже выполняется обновление радара.')
            operation = dict(previous['operation'], resume_of=str(previous['job_id']),
                refresh_id=previous['operation'].get('refresh_id') or str(previous['job_id']), profile_request=request)
            operation['priority_retry_cycle'] = (
                previous['operation'].get('priority_retry_cycle') or operation['refresh_id']
                if expected_job_id is not None else str(uuid4()))
            job = queue.enqueue(actor, key, ArtifactRef(previous['stock_artifact_id']), previous['search_phrase'],
                previous['stock_sha256'], None, operation=operation, connection=conn)
            data = dict(run['data'], current_job_id=str(job['job_id']), state='queued', error=None,
                        stop_requested=False)
            if rating_version is not None:
                data['stage_versions'] = dict(data.get('stage_versions') or {}, rating=rating_version)
            data.pop('stop_reason', None)
            conn.execute(runs.update().where(runs.c.run_id == row['run_id']).values(data=data))
            conn.execute(profiles.update().where(self._filter(actor.data_scope, source)).values(
                data=dict(row['data'], last_error=None), updated_at=func.now()))
            return dict(job, run_id=row['run_id'], _accepted=True)

    @staticmethod
    def _filter(scope, source):
        return (profiles.c.data_scope == scope) & (profiles.c.source == source)

    def list(self, scope):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(profiles).where(profiles.c.data_scope == scope)).mappings()]

    def read(self, scope, source):
        with self.engine.connect() as conn:
            row = conn.execute(select(profiles).where(self._filter(scope, source))).mappings().one_or_none()
            return dict(row) if row else None

    def _lock(self, conn, scope, source):
        conn.execute(insert(profiles).values(data_scope=scope, source=source, data=default_data())
                     .on_conflict_do_nothing())
        return dict(conn.execute(select(profiles).where(self._filter(scope, source))
                                 .with_for_update()).mappings().one())

    def save_keywords(self, scope, source, mode, keywords):
        with self.engine.begin() as conn:
            row = self._lock(conn, scope, source)
            data = dict(row['data'], keyword_mode=mode, keywords=keywords)
            conn.execute(profiles.update().where(self._filter(scope, source)).values(data=data, updated_at=func.now()))
            return dict(row, data=data)

    def save_schedule(self, actor, source, key, request, prepare):
        with self.engine.begin() as conn:
            row = self._lock(conn, actor.data_scope, source)
            receipts = CommandReceipts()
            operation, fingerprint = 'ai_radar.profile_schedule:' + source, digest(request)
            prior = receipts.repeat(conn, actor, operation, key, fingerprint)
            if prior is not None:
                return prior
            data = dict(row['data'], **prepare(row))
            conn.execute(profiles.update().where(self._filter(actor.data_scope, source)).values(
                data=data, updated_at=func.now()))
            result = dict(run_id=str(row['run_id']) if row['run_id'] else None, data=data)
            receipts.finish(conn, actor, operation, key, fingerprint, result)
            return result

    @staticmethod
    def _replay(conn, actor, key, request):
        row = conn.execute(select(*VIEW).where(jobs.c.data_scope == actor.data_scope,
            jobs.c.actor_id == actor.actor_id, jobs.c.command_key == key)).mappings().one_or_none()
        if row is None:
            return None
        operation = row['operation'] or {}
        if operation.get('kind') != 'profile_refresh' or operation.get('profile_request') != request:
            raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ команды уже использован с другими параметрами.')
        return dict(row, run_id=UUID(operation['run_id']))

    def replay(self, actor, key, request):
        with self.engine.connect() as conn:
            return self._replay(conn, actor, key, request)

    def enqueue_refresh(self, queue, actor, source, key, request, prepare):
        with self.engine.begin() as conn:
            row = self._lock(conn, actor.data_scope, source)
            replay = self._replay(conn, actor, key, request)
            if replay is not None:
                return dict(replay, _accepted=False)
            active = conn.execute(select(jobs.c.job_id).where(jobs.c.data_scope == actor.data_scope,
                ACTIVE, LANE == 'analysis').limit(1)).scalar_one_or_none()
            if active is not None:
                raise CoreError('PROCESS_ACTIVE', 'В этой области доступа уже выполняется обновление радара.')
            identifier = row['run_id'] or uuid4()
            prepared = prepare(row, identifier)
            operation = prepared['operation']
            job = queue.enqueue(actor, key, prepared['ref'], 'Профиль предприятия', prepared['sha256'],
                                None, operation=operation, connection=conn)
            run = conn.execute(select(runs.c.data).where(runs.c.run_id == identifier,
                runs.c.data_scope == actor.data_scope).with_for_update()).scalar_one_or_none()
            # Keep saved results and the generated-keyword cache; checkpoints belong to a job.
            data = dict(run or {}, profile_source=source, source=source, name=operation['name'],
                        current_job_id=str(job['job_id']), state='queued', phase='queued',
                        checkpoint={}, refresh_checkpoint={}, budgets=operation['budgets'],
                        search_attempts=0, detail_attempts=0, model_attempts=0, processed=0,
                        environment='simulation' if source == 'test_api' else 'kontur')
            for field in ('error', 'stop_reason', 'stop_requested', 'limits', 'quotas', 'effective_budgets', 'pending_detail',
                          'individual_rating_job_id', 'last_individual_rating_job_id', 'metrics_job_id', 'last_metrics_job_id'):
                data.pop(field, None)
            conn.execute(insert(runs).values(run_id=identifier, data_scope=actor.data_scope,
                settings_snapshot_id=prepared['snapshot_id'], data=data).on_conflict_do_update(
                    index_elements=[runs.c.run_id], set_={'settings_snapshot_id':prepared['snapshot_id'], 'data':data}))
            profile_data = dict(row['data'], **prepared['profile_updates'])
            conn.execute(profiles.update().where(self._filter(actor.data_scope, source)).values(
                run_id=identifier, data=profile_data, updated_at=func.now()))
            return dict(job, run_id=identifier, _accepted=True)

    def enqueue_metrics(self, queue, actor, source, key, snapshot_id, ref, sha256, request):
        with self.engine.begin() as conn:
            row = self._lock(conn, actor.data_scope, source)
            prior = conn.execute(select(*VIEW).where(jobs.c.data_scope == actor.data_scope,
                jobs.c.actor_id == actor.actor_id, jobs.c.command_key == key)).mappings().one_or_none()
            if prior is not None:
                if prior['operation'].get('kind') != 'profile_metrics' or prior['operation'].get('profile_request') != request:
                    raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ относится к другой команде.')
                return dict(prior, run_id=UUID(prior['operation']['run_id']))
            if row['run_id'] is None or conn.scalar(select(analyses.c.analysis_id).where(
                    analyses.c.run_id == row['run_id'], analyses.c.data['analysis'].astext.is_not(None),
                    analyses.c.data['details_loaded'].as_boolean().is_(True)).limit(1)) is None:
                raise CoreError('PROFILE_NOT_INITIALIZED', 'Нет сохранённых подробных расчётов.')
            conn.execute(select(runs.c.run_id).where(runs.c.run_id == row['run_id']).with_for_update()).one()
            operation = dict(kind='profile_metrics', profile_source=source, source=source,
                run_id=str(row['run_id']), settings_snapshot_id=str(snapshot_id), profile_request=request)
            job = queue.enqueue(actor, key, ref, 'Обновление показателей', sha256, None,
                                operation=operation, connection=conn)
            conn.execute(runs.update().where(runs.c.run_id == row['run_id']).values(data=runs.c.data.op('||')({
                'metrics_job_id': str(job['job_id']), 'last_metrics_job_id': str(job['job_id']), 'metrics_checkpoint': {}})))
            return dict(job, run_id=row['run_id'])

    def resume_metrics(self, queue, actor, source, key, previous, *, expected_job_id=None):
        with self.engine.begin() as conn:
            row = self._lock(conn, actor.data_scope, source)
            prior = conn.execute(select(*VIEW).where(jobs.c.data_scope == actor.data_scope,
                jobs.c.actor_id == actor.actor_id, jobs.c.command_key == key)).mappings().one_or_none()
            if prior is not None:
                if prior['operation'].get('kind') != 'profile_metrics' or prior['operation'].get('run_id') != str(row['run_id']):
                    raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ относится к другой команде.')
                return dict(prior, run_id=row['run_id'], _accepted=False)
            if row['run_id'] is None:
                raise CoreError('CONFLICT', 'Нет пересчёта для продолжения.')
            run = conn.execute(select(runs.c.data).where(runs.c.run_id == row['run_id'],
                runs.c.data_scope == actor.data_scope).with_for_update()).scalar_one()
            previous = conn.execute(select(jobs).where(jobs.c.job_id == previous['job_id'],
                jobs.c.data_scope == actor.data_scope)).mappings().one_or_none()
            if (previous is None or previous['state'] != 'failed'
                    or run.get('last_metrics_job_id') != str(previous['job_id'])
                    or (previous['operation'] or {}).get('kind') != 'profile_metrics'
                    or previous['operation'].get('profile_source') != source
                    or previous['operation'].get('run_id') != str(row['run_id'])
                    or (expected_job_id is not None and (previous['job_id'] != expected_job_id
                        or (previous['error'] or {}).get('code') != 'AI_RADAR_JOB_INTERRUPTED'))):
                raise CoreError('CONFLICT', 'Прерванный пересчёт уже заменён другой командой.')
            operation = dict(previous['operation'], resume_of=str(previous['job_id']),
                metrics_refresh_id=previous['operation'].get('metrics_refresh_id') or str(previous['job_id']))
            job = queue.enqueue(actor, key, ArtifactRef(previous['stock_artifact_id']), previous['search_phrase'],
                previous['stock_sha256'], None, operation=operation, connection=conn)
            conn.execute(runs.update().where(runs.c.run_id == row['run_id']).values(data=runs.c.data.op('||')({
                'metrics_job_id': str(job['job_id']), 'last_metrics_job_id': str(job['job_id'])})))
            return dict(job, run_id=row['run_id'], _accepted=True)

    def upsert_card(self, run_id: UUID, candidate_id: str, data: dict, *, connection=None) -> dict:
        identifier = uuid4()
        # The primary key is immutable, including when a caller supplies another UUID.
        payload = dict(data, candidate_id=candidate_id, analysis_id=str(identifier))
        statement = insert(analyses).values(analysis_id=identifier, run_id=run_id,
                                           candidate_id=candidate_id, data=payload)
        merged = analyses.c.data.op('||')({key:value for key,value in payload.items() if key != 'analysis_id'})
        merged = merged.op('||')(func.jsonb_build_object('analysis_id', cast(analyses.c.analysis_id, String)))
        statement = statement.on_conflict_do_update(index_elements=[analyses.c.run_id, analyses.c.candidate_id],
                                                     set_={'data': merged}).returning(analyses.c.data)
        with (nullcontext(connection) if connection is not None else self.engine.begin()) as conn:
            return conn.execute(statement).scalar_one()

    def set_run_snapshot(self, scope: str, run_id: UUID, snapshot_id: UUID) -> None:
        with self.engine.begin() as conn:
            changed = conn.execute(runs.update().where(runs.c.data_scope == scope, runs.c.run_id == run_id)
                                  .values(settings_snapshot_id=snapshot_id))
            if changed.rowcount != 1:
                raise CoreError('NOT_FOUND', 'Профиль не найден в этой области доступа.')

    def finish_refresh(self, scope: str, source: str, job_id: UUID, error: dict | None = None, *,
                       connection=None, require_lease=False) -> None:
        with (nullcontext(connection) if connection is not None else self.engine.begin()) as conn:
            row = conn.execute(select(profiles).where(self._filter(scope, source)).with_for_update()).mappings().one_or_none()
            if row is None or row['run_id'] is None:
                return
            run = conn.execute(select(runs.c.data).where(runs.c.data_scope == scope,
                runs.c.run_id == row['run_id']).with_for_update()).scalar_one()
            if run.get('current_job_id') != str(job_id):
                if require_lease:
                    raise CoreError('AI_RADAR_JOB_INTERRUPTED', 'Исполнение больше не владеет обновлением профиля.')
                return
            if require_lease and conn.scalar(select(jobs.c.job_id).where(jobs.c.job_id == job_id,
                    jobs.c.data_scope == scope, jobs.c.state == 'running', jobs.c.lease_until > func.now())) is None:
                raise CoreError('AI_RADAR_JOB_INTERRUPTED', 'Истёк срок владения обновлением профиля.')
            now = datetime.now(timezone.utc).isoformat()
            data = dict(row['data'], last_error=error)
            if run.get('quotas') is not None:
                data['quotas'] = run['quotas']
            elif run.get('limits') == [] and source == 'test_api':
                data['quotas'] = {'search':200, 'detail':200}
            if error is None:
                data['last_refresh_at'] = now
            conn.execute(profiles.update().where(self._filter(scope, source)).values(data=data, updated_at=func.now()))
            if error is not None:
                conn.execute(runs.update().where(runs.c.run_id == row['run_id']).values(
                    data=dict(run, state='failed', phase='failed', error=error)))

    def due(self, now):
        with self.engine.connect() as conn:
            rows = conn.execute(select(profiles).where(profiles.c.data['schedule']['enabled'].as_boolean().is_(True))).mappings()
            return [dict(row) for row in rows if row['data'].get('next_due_at')
                    and datetime.fromisoformat(row['data']['next_due_at']) <= now]

    def schedule_failed(self, scope, source, expected_due, error, now, next_due_at):
        with self.engine.begin() as conn:
            row = conn.execute(select(profiles).where(self._filter(scope, source)).with_for_update()).mappings().one_or_none()
            if row is None or row['data'].get('next_due_at') != expected_due:
                return
            data = dict(row['data'], last_error=error, next_due_at=next_due_at)
            conn.execute(profiles.update().where(self._filter(scope, source)).values(data=data, updated_at=func.now()))
            if row['run_id']:
                conn.execute(runs.update().where(runs.c.run_id == row['run_id'], runs.c.data_scope == scope)
                    .values(data=runs.c.data.op('||')({'state':'failed', 'error':error, 'phase':'failed'})))


class ProfileExecutionRepository:
    """Bind every execution write to its current run owner inside one transaction."""
    def __init__(self, profiles, repository, scope, run_id, job_id, *, owner_field='current_job_id'):
        if owner_field not in {'current_job_id', 'individual_rating_job_id', 'metrics_job_id'}:
            raise ValueError('Unsupported execution owner field')
        self.profiles, self.repository = profiles, repository
        self.scope, self.run_id, self.job_id = scope, run_id, job_id
        self.owner_field = owner_field

    def _write(self, method, *args):
        with self.profiles.engine.begin() as conn:
            owner = conn.execute(select(runs.c.data[self.owner_field].astext).where(
                runs.c.data_scope == self.scope, runs.c.run_id == self.run_id).with_for_update()).scalar_one_or_none()
            live = conn.scalar(select(jobs.c.job_id).where(jobs.c.job_id == self.job_id,
                jobs.c.data_scope == self.scope, jobs.c.state == 'running', jobs.c.lease_until > func.now()))
            if owner != str(self.job_id) or live is None:
                raise CoreError('AI_RADAR_JOB_INTERRUPTED', 'Исполнение больше не владеет обновлением профиля.')
            return method(*args, connection=conn)

    def update(self, scope, run_id, data):
        if scope != self.scope or run_id != self.run_id:
            raise CoreError('NOT_FOUND', 'Профиль не принадлежит этому исполнению.')
        return self._write(self.repository.update, scope, run_id, data)

    def upsert_card(self, run_id, candidate_id, data):
        self._run(run_id)
        return self._write(self.profiles.upsert_card, run_id, candidate_id, data)

    def prepare_card(self, run_id, candidate_id, tender):
        self._run(run_id)
        return self._write(self.repository.prepare_card, run_id, candidate_id, tender)

    def archive_page(self, run_id, page):
        self._run(run_id)
        return self._write(self.repository.archive_page, run_id, page)

    def report(self, job_id, run_id, data):
        self._run(run_id)
        if job_id != self.job_id:
            raise CoreError('AI_RADAR_JOB_INTERRUPTED', 'Отчёт принадлежит другому исполнению.')
        return self._write(self.repository.report, job_id, run_id, data)

    def reserve_rating(self, job_id, run_id, result):
        self._run(run_id)
        if job_id != self.job_id:
            raise CoreError('AI_RADAR_JOB_INTERRUPTED', 'Оценка принадлежит другому исполнению.')
        return self._write(self.repository.reserve_rating, job_id, run_id, result)

    def finish_refresh(self, scope, source, job_id, error=None):
        if scope != self.scope or job_id != self.job_id or self.owner_field != 'current_job_id':
            raise CoreError('AI_RADAR_JOB_INTERRUPTED', 'Исполнение не владеет завершением профиля.')
        # Use the same profile→run lock order as enqueue/resume, with ownership
        # and live lease checked inside that single completion transaction.
        return self.profiles.finish_refresh(scope, source, job_id, error, require_lease=True)

    def _run(self, run_id):
        if run_id != self.run_id:
            raise CoreError('NOT_FOUND', 'Профиль не принадлежит этому исполнению.')

    def __getattr__(self, name):
        if name in {'read', 'cards', 'source_pages', 'read_reports'}:
            return getattr(self.repository, name)
        raise AttributeError(name)

