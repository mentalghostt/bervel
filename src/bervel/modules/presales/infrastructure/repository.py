"""Transactional presales storage; no reads of another business module's tables."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

from sqlalchemy import select, update, delete, func, text, Integer, case

from bervel.core.errors import CoreError
from bervel.core.infrastructure.commands import CommandReceipts, digest, lock
from bervel.modules.presales.infrastructure.tables import settings_snapshots, requests, revisions, lines, jobs


def utcnow():
    return datetime.now(timezone.utc)


def public(value):
    return json.loads(json.dumps(value, default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item),
                                 allow_nan=False))


def identifier(value):
    return value if isinstance(value, UUID) else UUID(str(value))


JOB_VIEW_FIELDS = ('job_id', 'state', 'phase', 'error', 'stop_requested', 'progress')


def job_view(row):
    return public({key: row[key] for key in JOB_VIEW_FIELDS}) if row else None


class PostgresPresalesRepository:
    def __init__(self, engine):
        self.engine = engine
        self.receipts = CommandReceipts()

    def replay(self, actor, operation, key, payload):
        with self.engine.begin() as conn:
            return self.receipts.repeat(conn, actor, operation, key, digest(payload))

    def _command(self, actor, operation, key, payload, execute):
        with self.engine.begin() as conn:
            fingerprint = digest(payload)
            previous = self.receipts.repeat(conn, actor, operation, key, fingerprint)
            if previous is not None:
                return previous
            result = public(execute(conn))
            self.receipts.finish(conn, actor, operation, key, fingerprint, result)
            return result

    @staticmethod
    def _settings(conn, scope):
        row = conn.execute(select(settings_snapshots).where(settings_snapshots.c.data_scope == scope)
                           .order_by(settings_snapshots.c.version_no.desc()).limit(1)).mappings().first()
        return public(dict(row)) if row else None

    def read_settings(self, scope):
        with self.engine.connect() as conn:
            return self._settings(conn, scope)

    def save_settings(self, actor, base_snapshot_id, settings, key):
        payload = {'base_snapshot_id': str(base_snapshot_id) if base_snapshot_id else None, 'settings': settings}

        def execute(conn):
            lock(conn, f'presales.settings:{actor.data_scope}')
            head = self._settings(conn, actor.data_scope)
            if (head['snapshot_id'] if head else None) != payload['base_snapshot_id']:
                raise CoreError('CONFLICT', 'Настройки изменились. Обновите страницу перед сохранением.')
            record = dict(snapshot_id=uuid4(), data_scope=actor.data_scope,
                          version_no=head['version_no'] + 1 if head else 1,
                          settings=deepcopy(settings), created_at=utcnow())
            conn.execute(settings_snapshots.insert().values(**record))
            return record
        return self._command(actor, 'presales.settings.save', key, payload, execute)

    @staticmethod
    def _request(conn, scope, request_id, *, for_update=False):
        statement = select(requests).where(requests.c.data_scope == scope, requests.c.request_id == identifier(request_id))
        if for_update:
            statement = statement.with_for_update()
        row = conn.execute(statement).mappings().first()
        if row is None:
            raise CoreError('NOT_FOUND', 'Заявка не найдена в этой области доступа.')
        return row

    @staticmethod
    def _revision(conn, scope, request_id, revision_id):
        row = conn.execute(select(revisions).where(revisions.c.data_scope == scope,
            revisions.c.request_id == identifier(request_id), revisions.c.revision_id == identifier(revision_id))).mappings().first()
        if row is None:
            raise CoreError('NOT_FOUND', 'Редакция заявки не найдена.')
        return row

    @staticmethod
    def _insert_revision(conn, actor, request_id, revision_id, number, parent, inputs, decisions):
        stamp, job_id = utcnow(), uuid4()
        frozen = deepcopy(inputs)
        evaluated = datetime.fromisoformat(frozen.get('evaluated_at', stamp.isoformat()))
        conn.execute(revisions.insert().values(revision_id=revision_id, request_id=request_id,
            data_scope=actor.data_scope, revision_number=number, parent_revision_id=parent, created_at=stamp,
            evaluated_at=evaluated, state='queued', phase='read_sources', inputs=public(frozen),
            decisions=public(decisions), artifacts={'pdf': None, 'docx': None}))
        conn.execute(jobs.insert().values(job_id=job_id, request_id=request_id, revision_id=revision_id,
            data_scope=actor.data_scope, actor_id=actor.actor_id, permissions=sorted(actor.permissions),
            state='queued', phase='read_sources', stop_requested=False, cache={}, progress={},
            created_at=stamp, updated_at=stamp))
        return {'request_id': str(request_id), 'revision_id': str(revision_id), 'job_id': str(job_id), 'state': 'queued'}

    def create_request(self, actor, artifact_ids, inputs, key):
        payload = {'artifact_ids': sorted(artifact_ids)}

        def execute(conn):
            request_id, revision_id, stamp = uuid4(), uuid4(), utcnow()
            title = ', '.join(source['filename'] for source in inputs['sources'])
            conn.execute(requests.insert().values(request_id=request_id, data_scope=actor.data_scope,
                title=title, current_revision_id=revision_id, created_at=stamp, updated_at=stamp))
            return self._insert_revision(conn, actor, request_id, revision_id, 1, None, inputs, {})
        return self._command(actor, 'presales.request.create', key, payload, execute)

    def create_revision(self, actor, request_id, base_revision_id, kind, decisions, inputs, key, *, intent=None):
        payload = intent or {'request_id': str(request_id), 'base_revision_id': str(base_revision_id),
                   'kind': kind, 'decisions': decisions}

        def execute(conn):
            head = self._request(conn, actor.data_scope, request_id, for_update=True)
            if head['current_revision_id'] != identifier(base_revision_id):
                raise CoreError('CONFLICT', 'Откройте актуальную редакцию перед созданием новой.')
            active = conn.execute(select(jobs.c.job_id).where(jobs.c.request_id == identifier(request_id),
                jobs.c.state.in_(['queued', 'running']))).first()
            if active:
                raise CoreError('PROCESS_ACTIVE', 'Сначала дождитесь анализа или остановите его.')
            base = self._revision(conn, actor.data_scope, request_id, base_revision_id)
            revision_id = uuid4()
            receipt = self._insert_revision(conn, actor, identifier(request_id), revision_id,
                base['revision_number'] + 1, identifier(base_revision_id), inputs, decisions)
            conn.execute(update(requests).where(requests.c.request_id == identifier(request_id)).values(
                current_revision_id=revision_id, updated_at=utcnow()))
            return receipt
        return self._command(actor, 'presales.revision.create', key, payload, execute)

    def list_requests(self, scope, cursor=0, limit=30):
        with self.engine.connect() as conn:
            selected = conn.execute(select(requests, revisions.c.state, revisions.c.phase)
                .join(revisions, revisions.c.revision_id == requests.c.current_revision_id)
                .where(requests.c.data_scope == scope)
                .order_by(requests.c.created_at.desc(), requests.c.request_id).offset(cursor).limit(limit + 1)).mappings().all()
        return {'items': public([dict(row) for row in selected[:limit]]),
                'next_cursor': cursor + limit if len(selected) > limit else None}

    def read_request(self, scope, request_id):
        with self.engine.connect() as conn:
            row = dict(self._request(conn, scope, request_id))
            history = conn.execute(select(revisions.c.revision_id, revisions.c.revision_number,
                revisions.c.created_at, revisions.c.state, revisions.c.phase)
                .where(revisions.c.request_id == identifier(request_id), revisions.c.data_scope == scope)
                .order_by(revisions.c.revision_number.desc())).mappings().all()
            current = next(item for item in history if item['revision_id'] == row['current_revision_id'])
            row.update(state=current['state'], phase=current['phase'])
            job = conn.execute(select(*(jobs.c[key] for key in JOB_VIEW_FIELDS))
                .where(jobs.c.revision_id == row['current_revision_id'])).mappings().first()
            return {'request': public(row), 'revisions': public([dict(item) for item in history]), 'job': job_view(job)}

    def read_revision(self, scope, request_id, revision_id):
        with self.engine.connect() as conn:
            return public(dict(self._revision(conn, scope, request_id, revision_id)))

    def revision_summary(self, scope, request_id, revision_id):
        # JSONB projection avoids moving the full warehouse and all extracted lists over HTTP reads.
        with self.engine.connect() as conn:
            row = conn.execute(select(revisions.c.revision_id, revisions.c.request_id, revisions.c.revision_number,
                revisions.c.parent_revision_id, revisions.c.created_at, revisions.c.evaluated_at,
                revisions.c.state, revisions.c.phase, revisions.c.decisions, revisions.c.offer, revisions.c.artifacts,
                revisions.c.analysis, revisions.c.inputs['settings_snapshot_id'].label('settings_snapshot_id'),
                revisions.c.inputs['company']['id'].label('company_snapshot_id'),
                revisions.c.inputs['sources'].label('sources'),
                revisions.c.extracted.op('-')('lines').op('-')('lists').label('extracted'))
                .where(revisions.c.data_scope == scope, revisions.c.request_id == identifier(request_id),
                       revisions.c.revision_id == identifier(revision_id))).mappings().first()
            if row is None:
                raise CoreError('NOT_FOUND', 'Редакция заявки не найдена.')
            value = dict(row)
            extracted = value['extracted'] or {}
            extracted['lists'] = extracted.pop('list_summaries', [])
            value['extracted'] = extracted
            analysis = value.pop('analysis') or {}
            value.update({field: analysis.get(field) for field in ('metrics', 'review', 'score', 'model_error')})
            value['warnings'] = extracted.get('warnings', [])
            value['job'] = job_view(conn.execute(select(*(jobs.c[key] for key in JOB_VIEW_FIELDS))
                .where(jobs.c.revision_id == identifier(revision_id))).mappings().first())
            return public(value)

    def read_lines(self, scope, request_id, revision_id, cursor=0, limit=50):
        with self.engine.connect() as conn:
            self._revision(conn, scope, request_id, revision_id)
            values = conn.execute(select(lines).where(lines.c.revision_id == identifier(revision_id),
                lines.c.ordinal >= cursor).order_by(lines.c.ordinal).limit(limit + 1)).mappings().all()
            return {'items': [row['record'] for row in values[:limit]],
                    'next_cursor': values[limit - 1]['ordinal'] + 1 if len(values) > limit else None}

    def all_lines(self, owned):
        with self.engine.connect() as conn:
            return list(conn.execute(select(lines.c.record).where(lines.c.revision_id == identifier(owned['revision_id']))
                                     .order_by(lines.c.ordinal)).scalars())

    def read_source_lines(self, scope, request_id, revision_id, list_id, cursor=0, limit=50):
        parameters = {'scope': scope, 'request': identifier(request_id), 'revision': identifier(revision_id),
                      'list': list_id, 'cursor': cursor, 'count': limit + 1}
        source = """FROM presales_revisions r
            CROSS JOIN LATERAL jsonb_array_elements(COALESCE(r.extracted->'lists','[]'::jsonb)) AS source(value)
            WHERE r.data_scope=:scope AND r.request_id=:request AND r.revision_id=:revision
              AND source.value->>'list_id'=:list"""
        with self.engine.connect() as conn:
            if not conn.execute(text('SELECT EXISTS(SELECT 1 ' + source + ')'), parameters).scalar_one():
                raise CoreError('NOT_FOUND', 'Исходный список не найден в этой заявке.')
            # Slice in PostgreSQL: the HTTP path never fetches a whole competing document list.
            statement = """SELECT item.value AS record FROM (
                SELECT source.value """ + source + """) chosen
                CROSS JOIN LATERAL jsonb_array_elements(COALESCE(chosen.value->'lines','[]'::jsonb))
                    WITH ORDINALITY AS item(value, ordinal)
                WHERE item.ordinal>:cursor ORDER BY item.ordinal LIMIT :count"""
            records = list(conn.execute(text(statement), parameters).scalars())
            return {'items': records[:limit], 'next_cursor': cursor + limit if len(records) > limit else None}

    def claim(self):
        with self.engine.begin() as conn:
            stamp = utcnow()
            expired = conn.execute(select(jobs).where(jobs.c.state == 'running', jobs.c.lease_until < stamp)
                                   .with_for_update(skip_locked=True)).mappings().all()
            for old in expired:
                error = {'code': 'RECOVERY_REQUIRED', 'message': 'Обработка прервалась. Выполненные стадии сохранены; продолжите явно.'}
                conn.execute(update(jobs).where(jobs.c.job_id == old['job_id']).values(state='failed', error=error,
                    lease_token=None, lease_until=None, updated_at=stamp))
                conn.execute(update(revisions).where(revisions.c.revision_id == old['revision_id']).values(state='failed'))
            row = conn.execute(select(jobs).where(jobs.c.state == 'queued')
                .order_by(jobs.c.created_at).with_for_update(skip_locked=True).limit(1)).mappings().first()
            if row is None:
                return None
            token = uuid4()
            conn.execute(update(jobs).where(jobs.c.job_id == row['job_id']).values(state='running',
                lease_token=token, lease_until=stamp + timedelta(seconds=180), updated_at=stamp, error=None))
            conn.execute(update(revisions).where(revisions.c.revision_id == row['revision_id']).values(state='running'))
            return public({**dict(row), 'state': 'running', 'lease_token': token, 'error': None})

    @staticmethod
    def _owned(conn, owned, *, include_cache=False):
        columns = list(jobs.c) if include_cache else [column for column in jobs.c if column.name != 'cache']
        row = conn.execute(select(*columns).where(jobs.c.job_id == identifier(owned['job_id']),
            jobs.c.lease_token == identifier(owned['lease_token']), jobs.c.state == 'running')
            .with_for_update()).mappings().first()
        if row is None:
            raise CoreError('STALE_ATTEMPT', 'Попытка больше не владеет заданием.')
        return row

    def heartbeat(self, job_id, token):
        with self.engine.begin() as conn:
            row = self._owned(conn, {'job_id': job_id, 'lease_token': token})
            conn.execute(update(jobs).where(jobs.c.job_id == identifier(job_id)).values(
                lease_until=utcnow() + timedelta(seconds=180), updated_at=utcnow()))
            return bool(row['stop_requested'])

    def read_job_cache(self, owned):
        with self.engine.begin() as conn:
            return deepcopy(self._owned(conn, owned,include_cache=True)['cache'])

    def checkpoint(self, owned, key, value):
        with self.engine.begin() as conn:
            row = self._owned(conn, owned)
            count = func.coalesce(jobs.c.progress['completed_steps'].astext.cast(Integer),0) + case(
                (jobs.c.cache.has_key(key),0),else_=1)
            progress = jobs.c.progress.op('||')(func.jsonb_build_object(
                'last_checkpoint',key,'completed_steps',count))
            if key == 'detail:match_progress':
                progress = progress.op('||')(public(value))
            conn.execute(update(jobs).where(jobs.c.job_id == row['job_id']).values(
                cache=jobs.c.cache.op('||')({key:public(value)}),updated_at=utcnow(),progress=progress))

    def set_phase(self, owned, phase, **result):
        allowed = {'extracted', 'analysis', 'offer', 'artifacts'}
        if set(result) - allowed:
            raise ValueError('Invalid revision fields')
        with self.engine.begin() as conn:
            row = self._owned(conn, owned)
            saved = public(result)
            records = None
            if saved.get('analysis') is not None:
                records = saved['analysis'].pop('lines', None)
            if saved.get('extracted') is not None:
                extracted = saved['extracted']
                records = extracted.get('lines', []) if records is None else records
                extracted['list_summaries'] = [{key: item.get(key) for key in ('list_id', 'artifact_id', 'filename', 'complete')}
                    | {'position_count': len(item.get('lines', [])), 'attribute_count': sum(len(line.get('requirements', [])) for line in item.get('lines', []))}
                    for item in extracted.get('lists', [])]
            if records is not None:
                conn.execute(delete(lines).where(lines.c.revision_id == row['revision_id']))
                if records:
                    conn.execute(lines.insert(), [dict(revision_id=row['revision_id'], line_id=item['line_id'],
                        ordinal=index, record=item) for index, item in enumerate(records)])
            conn.execute(update(revisions).where(revisions.c.revision_id == row['revision_id']).values(phase=phase, **saved))
            conn.execute(update(jobs).where(jobs.c.job_id == row['job_id']).values(phase=phase, updated_at=utcnow()))
            if saved.get('extracted'):
                extracted = saved['extracted']
                conn.execute(update(requests).where(requests.c.request_id == row['request_id'],
                    requests.c.current_revision_id == row['revision_id']).values(
                        title=extracted.get('title') or 'Заявка клиента', client=extracted.get('client'), updated_at=utcnow()))

    def finish_job(self, owned, *, state='complete', error=None):
        with self.engine.begin() as conn:
            row = self._owned(conn, owned)
            if row['stop_requested'] and state == 'complete':
                state = 'stopped'
            phase = 'complete' if state == 'complete' else row['phase']
            conn.execute(update(jobs).where(jobs.c.job_id == row['job_id']).values(state=state, phase=phase,
                error=public(error), lease_token=None, lease_until=None, updated_at=utcnow()))
            conn.execute(update(revisions).where(revisions.c.revision_id == row['revision_id']).values(state=state, phase=phase))
            conn.execute(update(requests).where(requests.c.request_id == row['request_id']).values(updated_at=utcnow()))

    def _job_command(self, actor, request_id, job_id, key, *, resume):
        payload = {'request_id': str(request_id), 'job_id': str(job_id)}

        def execute(conn):
            row = conn.execute(select(jobs).where(jobs.c.job_id == identifier(job_id),
                jobs.c.request_id == identifier(request_id), jobs.c.data_scope == actor.data_scope).with_for_update()).mappings().first()
            if row is None:
                raise CoreError('NOT_FOUND', 'Задание не найдено.')
            # Worker writes use job -> revision -> request. Stop/Resume must not reverse that order.
            head = self._request(conn, actor.data_scope, request_id, for_update=True)
            if resume:
                if head['current_revision_id'] != row['revision_id']:
                    raise CoreError('CONFLICT', 'Старая редакция доступна только для чтения.')
                if row['state'] not in ('failed', 'stopped'):
                    raise CoreError('CONFLICT', 'Продолжить можно только остановленное или завершившееся ошибкой задание.')
                # An explicit manager command permits retrying an unknown call; never do this on lease recovery.
                invalid_output = (row['error'] or {}).get('code') == 'MODEL_INVALID_OUTPUT'
                cache = {k: v for k, v in row['cache'].items() if not (k.startswith('model:') and (
                    v.get('state') in ('started', 'unknown') or
                    invalid_output and v.get('state') == 'complete' and v.get('validation_error')))}
                conn.execute(update(jobs).where(jobs.c.job_id == row['job_id']).values(state='queued',
                    stop_requested=False, error=None, cache=cache, lease_token=None, lease_until=None, updated_at=utcnow()))
                conn.execute(update(revisions).where(revisions.c.revision_id == row['revision_id']).values(state='queued'))
                state = 'queued'
            else:
                state = 'stopped' if row['state'] == 'queued' else row['state']
                conn.execute(update(jobs).where(jobs.c.job_id == row['job_id']).values(
                    stop_requested=True, state=state, updated_at=utcnow()))
                if state == 'stopped':
                    conn.execute(update(revisions).where(revisions.c.revision_id == row['revision_id']).values(state=state))
            return dict(request_id=str(request_id), revision_id=str(row['revision_id']), job_id=str(job_id), state=state)
        return self._command(actor, 'presales.job.resume' if resume else 'presales.job.stop', key, payload, execute)

    def stop_job(self, actor, request_id, job_id, key):
        return self._job_command(actor, request_id, job_id, key, resume=False)

    def resume_job(self, actor, request_id, job_id, key):
        return self._job_command(actor, request_id, job_id, key, resume=True)
