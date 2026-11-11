from uuid import UUID, uuid4
from copy import deepcopy
from datetime import datetime, timezone
from contextlib import nullcontext
import hashlib
import json

from sqlalchemy import Column, DateTime, ForeignKey, String, Table, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as UUIDColumn, insert
from sqlalchemy.engine import Connection

from bervel.core.errors import CoreError
from bervel.core.infrastructure.database import metadata
from bervel.modules.ai_tender_radar.infrastructure.repository import jobs as radar_jobs
from bervel.modules.ai_tender_radar.models import USER_STOPPED


runs = Table('ai_radar_runs', metadata,
    Column('run_id', UUIDColumn(as_uuid=True), primary_key=True),
    Column('data_scope', String(128), nullable=False),
    Column('settings_snapshot_id', UUIDColumn(as_uuid=True), nullable=False),
    Column('data', JSONB, nullable=False),
    Column('created_at', DateTime(timezone=True), nullable=False, server_default=func.now()))
analyses = Table('ai_radar_analyses', metadata,
    Column('analysis_id', UUIDColumn(as_uuid=True), primary_key=True),
    Column('run_id', UUIDColumn(as_uuid=True), ForeignKey('ai_radar_runs.run_id'), nullable=False),
    Column('candidate_id', String(128), nullable=False), Column('data', JSONB, nullable=False))
reports = Table('ai_radar_reports', metadata,
    Column('job_id', UUIDColumn(as_uuid=True), primary_key=True),
    Column('run_id', UUIDColumn(as_uuid=True), ForeignKey('ai_radar_runs.run_id'), nullable=False),
    Column('data', JSONB, nullable=False))
pages = Table('ai_radar_search_pages', metadata,
    Column('page_id', String(64), primary_key=True),
    Column('run_id', UUIDColumn(as_uuid=True), ForeignKey('ai_radar_runs.run_id'), nullable=False),
    Column('data', JSONB, nullable=False))
legacy_groups = Table('ai_radar_legacy_groups',metadata,
    Column('data_scope',String(128),primary_key=True),
    Column('job_ids',JSONB,nullable=False),
    Column('created_at',DateTime(timezone=True),nullable=False,server_default=func.now()))

def _legacy_history_entry(created_at: datetime, job_ids: list[str]) -> dict:
    return dict(id='legacy', kind='legacy', title='Результаты до обновления',
                created_at=created_at, state='complete', card_count=len(job_ids))


class PostgresRunRepository:
    def __init__(self, engine):
        self.engine = engine

    def freeze_legacy(self, scope):
        """Explicit migration/import command; reads never create or extend this group."""
        from bervel.modules.ai_tender_radar.infrastructure.repository import PostgresAiTenderRadarRepository
        source=PostgresAiTenderRadarRepository(self.engine)
        identifiers=[]
        offset=0
        while True:
            page=source.cards(scope,50,offset)
            identifiers.extend(str(card['job_id']) for card in page['cards'])
            if page['next_offset'] is None:
                break
            offset=page['next_offset']
        with self.engine.begin() as conn:
            conn.execute(insert(legacy_groups).values(data_scope=scope,job_ids=identifiers).on_conflict_do_nothing())

    def legacy_entry(self, scope):
        with self.engine.connect() as conn:
            row=conn.execute(select(legacy_groups).where(legacy_groups.c.data_scope==scope)).mappings().one_or_none()
        return _legacy_history_entry(row['created_at'], row['job_ids']) if row is not None else None

    def legacy_cards(self, scope, limit=30, offset=0):
        from bervel.modules.ai_tender_radar.infrastructure.repository import PostgresAiTenderRadarRepository
        with self.engine.begin() as conn:
            # Keep the frozen membership and its page consistent with concurrent deletion.
            row = conn.execute(select(legacy_groups).where(legacy_groups.c.data_scope == scope)
                .with_for_update(read=True)).mappings().one_or_none()
            if row is None:
                return None
            identifiers = row['job_ids']
            page = PostgresAiTenderRadarRepository(self.engine).cards(scope, limit, offset, 'coverage',
                job_ids=[UUID(key) for key in identifiers], connection=conn)
            return dict(page, entry=_legacy_history_entry(row['created_at'], identifiers))

    def delete_legacy(self, scope: str) -> None:
        with self.engine.begin() as conn:
            identifiers = conn.execute(legacy_groups.delete().where(legacy_groups.c.data_scope == scope)
                .returning(legacy_groups.c.job_ids)).scalar_one_or_none()
            if identifiers is None:
                return
            # Named runs and their operations always have a non-null operation.
            owned = (radar_jobs.c.data_scope == scope) & radar_jobs.c.operation.is_(None) & (
                radar_jobs.c.job_id.in_([UUID(key) for key in identifiers]))
            active = conn.execute(select(radar_jobs.c.job_id).where(owned,
                radar_jobs.c.state.in_(('queued', 'running', 'waiting'))).limit(1)).scalar_one_or_none()
            if active is not None:
                raise CoreError('CONFLICT', 'Дождитесь завершения связанных заданий архива.')
            conn.execute(radar_jobs.delete().where(owned))

    def enqueue_operation(self, jobs, actor, key, ref, phrase, sha256, operation, *, initial=None, run_id=None):
        with self.engine.begin() as conn:
            data = self._lock_run(conn, actor.data_scope, run_id) if run_id is not None else None
            if run_id is not None and data is None:
                raise CoreError('NOT_FOUND', 'Прогон не найден в этой области доступа.')
            local_rating = (operation['kind'] == 'rating' and (operation.get('reference') or {}).get(
                'rating_version') == 'card-individual-rating-v1')
            if local_rating and (not data.get('profile_source') or data.get('state') != 'complete'):
                raise CoreError('CONFLICT', 'Отдельная оценка сохранённых карточек доступна после завершения профиля.')
            if operation['kind'] == 'question':
                card = conn.execute(select(analyses.c.data).where(analyses.c.run_id == run_id,
                    analyses.c.analysis_id == UUID(operation['analysis_id'])).with_for_update(read=True)).scalar_one_or_none()
                if card is None:
                    raise CoreError('NOT_FOUND', 'Карточка не принадлежит этому прогону.')
                if card.get('state') == 'analyzing':
                    raise CoreError('CONFLICT', 'Дождитесь завершения анализа этой карточки.')
            job = jobs.enqueue(actor, key, ref, phrase, sha256, None, operation=operation, connection=conn)
            if initial is not None:
                data = dict(initial, current_job_id=str(job['job_id']))
                conn.execute(insert(runs).values(run_id=job['job_id'], data_scope=actor.data_scope,
                    settings_snapshot_id=UUID(operation['settings_snapshot_id']), data=data).on_conflict_do_nothing())
            elif operation['kind'] == 'resume':
                # Merge only queue ownership; never overwrite the latest checkpoint with an API read.
                conn.execute(runs.update().where(runs.c.run_id == run_id, runs.c.data_scope == actor.data_scope)
                    .values(data=runs.c.data.op('||')({'state': 'queued', 'current_job_id': str(job['job_id'])})))
            elif local_rating:
                conn.execute(runs.update().where(runs.c.run_id == run_id, runs.c.data_scope == actor.data_scope)
                    .values(data=runs.c.data.op('||')({'individual_rating_job_id': str(job['job_id']),
                        'last_individual_rating_job_id': str(job['job_id']),
                        'individual_rating_version': 'card-individual-rating-v1'})))
            return job

    def release_rating_owner(self, scope, run_id, job_id):
        return self.release_owner(scope, run_id, job_id, 'individual_rating_job_id')

    def release_metrics_owner(self, scope, run_id, job_id):
        return self.release_owner(scope, run_id, job_id, 'metrics_job_id')

    def release_owner(self, scope, run_id, job_id, field):
        if field not in {'individual_rating_job_id', 'metrics_job_id'}:
            raise ValueError('Unsupported owner field')
        with self.engine.begin() as conn:
            data = self._lock_run(conn, scope, run_id)
            if data is not None and data.get(field) == str(job_id):
                data = dict(data)
                data.pop(field)
                conn.execute(runs.update().where(runs.c.run_id == run_id, runs.c.data_scope == scope).values(data=data))

    def ensure(self, scope, run_id, snapshot_id, data):
        with self.engine.begin() as conn:
            conn.execute(insert(runs).values(run_id=run_id, data_scope=scope,
                settings_snapshot_id=snapshot_id, data=data).on_conflict_do_nothing())

    def read(self, scope, run_id):
        with self.engine.connect() as conn:
            row = conn.execute(select(runs).where(runs.c.data_scope == scope, runs.c.run_id == run_id)).mappings().one_or_none()
        return dict(row) if row else None

    def list(self, scope, limit=30, offset=0):
        current_job_id = cast(runs.c.data['current_job_id'].astext, UUIDColumn(as_uuid=True))
        current_job = runs.outerjoin(radar_jobs, (radar_jobs.c.job_id == current_job_id)
            & (radar_jobs.c.data_scope == scope))
        card_count = select(func.count()).select_from(analyses).where(analyses.c.run_id == runs.c.run_id).scalar_subquery()
        with self.engine.connect() as conn:
            rows = conn.execute(select(runs, card_count.label('card_count'),
                radar_jobs.c.state.label('current_job_state'), radar_jobs.c.error.label('current_job_error'))
                .select_from(current_job).where(runs.c.data_scope == scope)
                .order_by(runs.c.created_at.desc(), runs.c.run_id).limit(limit + 1).offset(offset)).mappings().all()
        return {'runs': [dict(row) for row in rows[:limit]],
                'next_offset': offset + limit if len(rows) > limit else None}

    @staticmethod
    def _lock_run(conn: Connection, scope: str, run_id: UUID) -> dict | None:
        return conn.execute(select(runs.c.data).where(runs.c.data_scope == scope, runs.c.run_id == run_id)
                            .with_for_update()).scalar_one_or_none()

    def stop(self, scope: str, run_id: UUID) -> None:
        with self.engine.begin() as conn:
            data = self._lock_run(conn, scope, run_id)
            if data is None:
                raise CoreError('NOT_FOUND', 'Прогон не найден в этой области доступа.')
            current = UUID(data.get('current_job_id', str(run_id)))
            rating_owner = data.get('metrics_job_id') or data.get('individual_rating_job_id')
            if rating_owner and conn.scalar(select(radar_jobs.c.job_id).where(
                    radar_jobs.c.job_id == UUID(rating_owner), radar_jobs.c.data_scope == scope,
                    radar_jobs.c.state.in_(('queued', 'running')))) is not None:
                current = UUID(rating_owner)
            job_filter = (radar_jobs.c.data_scope == scope) & (radar_jobs.c.job_id == current)
            job = conn.execute(select(radar_jobs.c.state, radar_jobs.c.error).where(job_filter)
                .with_for_update()).mappings().one_or_none()
            if job is not None and (job['error'] or {}).get('code') == USER_STOPPED:
                return
            if job is None or job['state'] not in {'queued', 'running'}:
                raise CoreError('CONFLICT', 'Этот прогон уже завершился.')
            # A running worker acknowledges the request at a heartbeat and keeps
            # its queue slot until it exits. A queued job can stop immediately.
            stopped = {'state': 'failed', 'lease_until': None} if job['state'] == 'queued' else {}
            conn.execute(radar_jobs.update().where(job_filter).values(updated_at=func.now(),
                error={'code': USER_STOPPED, 'message': 'Прогон остановлен пользователем.'}, **stopped))

    def delete(self, scope: str, run_id: UUID) -> None:
        # The same lock protects enqueue_operation against deleting its source run.
        with self.engine.begin() as conn:
            data = self._lock_run(conn, scope, run_id)
            if data is None:
                return
            current_job_id = UUID(data.get('current_job_id', str(run_id)))
            report_jobs = select(reports.c.job_id).where(reports.c.run_id == run_id)
            owned_jobs = or_(radar_jobs.c.job_id.in_((run_id, current_job_id)),
                radar_jobs.c.operation['run_id'].astext == str(run_id), radar_jobs.c.job_id.in_(report_jobs))
            linked_jobs = owned_jobs | (radar_jobs.c.operation['source_run_id'].astext == str(run_id))
            active = conn.execute(select(radar_jobs.c.job_id).where(radar_jobs.c.data_scope == scope,
                linked_jobs, radar_jobs.c.state.in_(('queued', 'running', 'waiting'))).limit(1)).scalar_one_or_none()
            if active is not None:
                raise CoreError('CONFLICT', 'Дождитесь завершения прогона и связанных заданий.')
            if data.get('state') not in {'complete', 'failed'}:
                current = conn.execute(select(radar_jobs.c.state).where(radar_jobs.c.data_scope == scope,
                    radar_jobs.c.job_id == current_job_id)).scalar_one_or_none()
                if current != 'failed':
                    raise CoreError('CONFLICT', 'Удаление доступно после завершения прогона.')
            conn.execute(radar_jobs.delete().where(radar_jobs.c.data_scope == scope, owned_jobs))
            for table in (reports, pages, analyses):
                conn.execute(table.delete().where(table.c.run_id == run_id))
            conn.execute(runs.delete().where(runs.c.data_scope == scope, runs.c.run_id == run_id))

    def update(self, scope, run_id, data, *, connection=None):
        with (nullcontext(connection) if connection is not None else self.engine.begin()) as conn:
            conn.execute(runs.update().where(runs.c.run_id == run_id, runs.c.data_scope == scope).values(data=data))

    def cards(self, run_id):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(analyses).where(analyses.c.run_id == run_id)
                .order_by(analyses.c.candidate_id)).mappings()]

    def add_card(self, run_id, candidate_id, data):
        identifier = UUID(data['analysis_id']) if data.get('analysis_id') else uuid4()
        data = dict(data, analysis_id=str(identifier))
        with self.engine.begin() as conn:
            conn.execute(insert(analyses).values(analysis_id=identifier, run_id=run_id,
                candidate_id=candidate_id, data=data).on_conflict_do_nothing())
        return data

    def finalize_card(self, run_id, candidate_id, data):
        fields = {key: data[key] for key in ('analysis','completeness','fulfillment','assessment','review','state','error','selection','catalog_match_percent','stock_coverage_by_piece') if key in data}
        with self.engine.begin() as conn:
            conn.execute(analyses.update().where(analyses.c.run_id == run_id, analyses.c.candidate_id == candidate_id,
                analyses.c.data['state'].astext == 'analyzing').values(data=analyses.c.data.op('||')(fields)))

    def retry_failed_card(self, run_id, candidate_id, job_id):
        """Claim one manual retry and retain the complete failed result atomically."""
        with self.engine.begin() as conn:
            row = conn.execute(select(analyses).where(analyses.c.run_id == run_id,
                analyses.c.candidate_id == candidate_id).with_for_update()).mappings().one_or_none()
            if row is None:
                return None
            data = deepcopy(row['data'])
            if data.get('state') != 'failed' or not data.get('raw_tender') or data.get('retry_job_id') == job_id:
                return None
            previous = {key: value for key, value in data.items() if key != 'attempts'}
            data.setdefault('attempts', []).append({'resumed_by_job_id': job_id,
                'saved_at': datetime.now(timezone.utc).isoformat(), 'result': previous})
            data.update(state='analyzing', retry_job_id=job_id)
            data.pop('error', None)
            conn.execute(analyses.update().where(analyses.c.analysis_id == row['analysis_id']).values(data=data))
            return data

    def report(self, job_id, run_id, data, *, connection=None):
        with (nullcontext(connection) if connection is not None else self.engine.begin()) as conn:
            conn.execute(insert(reports).values(job_id=job_id, run_id=run_id, data=data).on_conflict_do_update(
                index_elements=[reports.c.job_id],set_={'data':data},
                where=(reports.c.run_id==run_id) & (reports.c.data['result']['status'].astext=='running')))

    def reserve_rating(self, job_id, run_id, result, *, connection=None):
        """One durable attempt per cohort, including interrupted/unknown attempts."""
        with (nullcontext(connection) if connection is not None else self.engine.begin()) as conn:
            conn.execute(select(runs.c.run_id).where(runs.c.run_id == run_id).with_for_update()).one()
            existing = conn.execute(select(reports).where(reports.c.run_id == run_id,
                reports.c.data['kind'].astext == 'rating',
                reports.c.data['result']['prompt_version'].astext == result['prompt_version'],
                reports.c.data['result']['fingerprint'].astext == result['fingerprint'])).mappings().first()
            if existing is not None:
                return dict(existing, created=False)
            data = {'kind': 'rating', 'result': result}
            conn.execute(insert(reports).values(job_id=job_id, run_id=run_id, data=data))
            return {'job_id': job_id, 'run_id': run_id, 'data': data, 'created': True}

    def read_reports(self, run_id):
        with self.engine.connect() as conn:
            saved = [dict(row) for row in conn.execute(select(reports, radar_jobs.c.state.label('job_state'),
                radar_jobs.c.command_key.label('_command_key'), radar_jobs.c.created_at.label('_created_at'),
                radar_jobs.c.error.label('_job_error')).outerjoin(
                radar_jobs, radar_jobs.c.job_id == reports.c.job_id).where(reports.c.run_id == run_id)
                .order_by(radar_jobs.c.created_at.asc().nullsfirst(), reports.c.job_id)).mappings()]
            scope = select(runs.c.data_scope).where(runs.c.run_id == run_id).scalar_subquery()
            # Exclude the reports this read actually captured. A reply committed
            # between these SELECTs must leave its user message visible here.
            captured = [entry['job_id'] for entry in saved]
            questions = conn.execute(select(radar_jobs.c.job_id, radar_jobs.c.operation,
                radar_jobs.c.command_key, radar_jobs.c.created_at, radar_jobs.c.state, radar_jobs.c.error).where(
                    radar_jobs.c.data_scope == scope, radar_jobs.c.operation['kind'].astext == 'question',
                    radar_jobs.c.operation['run_id'].astext == str(run_id),
                    ~radar_jobs.c.job_id.in_(captured))).mappings().all()
        ordered = []
        for entry in saved:
            stamp, command_key, error = (entry.pop(key) for key in ('_created_at', '_command_key', '_job_error'))
            if entry['data'].get('kind') == 'question' and command_key is not None:
                entry['data'] = dict(entry['data'], command_key=command_key, state=entry['job_state'])
                if error:
                    entry['data']['error'] = error
            ordered.append((stamp, entry))
        for question in questions:
            operation = question['operation']
            data = {'kind': 'question', 'analysis_id': operation.get('analysis_id'),
                'question': operation.get('question'), 'command_key': question['command_key'],
                'result': None, 'state': question['state']}
            if question['error']:
                data['error'] = question['error']
            ordered.append((question['created_at'], {'job_id': question['job_id'], 'run_id': run_id,
                'job_state': question['state'], 'data': data}))
        ordered.sort(key=lambda pair: (pair[0] or datetime.min.replace(tzinfo=timezone.utc), str(pair[1]['job_id'])))
        return [entry for _, entry in ordered]

    def archive_page(self, run_id, page, *, connection=None):
        payload = json.dumps(page, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        page_id = hashlib.sha256((str(run_id) + payload).encode()).hexdigest()
        with (nullcontext(connection) if connection is not None else self.engine.begin()) as conn:
            conn.execute(insert(pages).values(page_id=page_id, run_id=run_id, data=page).on_conflict_do_nothing())
        return page_id

    def source_pages(self, run_id):
        with self.engine.connect() as conn:
            return [dict(row) for row in conn.execute(select(pages).where(pages.c.run_id == run_id)).mappings()]

    def prepare_card(self, run_id, candidate_id, tender, *, connection=None):
        with (nullcontext(connection) if connection is not None else self.engine.begin()) as conn:
            conn.execute(analyses.update().where(analyses.c.run_id == run_id, analyses.c.candidate_id == candidate_id,
                analyses.c.data['state'].astext == 'analyzing').values(data=analyses.c.data.op('||')(
                    {'tender': tender, 'input_finalized': True})))
