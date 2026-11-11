"""Fixed, persistent radar profiles and scheduling in the existing worker."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata
from zoneinfo import ZoneInfo

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef
from bervel.core.services import authorize, validate_key
from bervel.modules.company_settings import search_phrases
from uuid import UUID


SOURCES = {'test_api': 'Симуляция контур.закупки', 'kontur_api': 'контур.закупки'}
MOSCOW = ZoneInfo('Europe/Moscow')
DEFAULT_SCHEDULE = dict(enabled=False, every_days=1, time='09:00', timezone='Europe/Moscow')
_UNSET = object()


def default_data() -> dict:
    return dict(keyword_mode='auto', keywords=[], schedule=dict(DEFAULT_SCHEDULE),
                last_refresh_at=None, next_due_at=None, reanalyze_existing=False, auto_reanalyze_existing=False)


def validate_keywords(mode, keywords) -> list[str]:
    if mode not in {'auto', 'manual'} or not isinstance(keywords, list):
        raise CoreError('VALIDATION_ERROR', 'Выберите режим ключевых слов и список фраз.')
    if mode == 'auto' and keywords:
        raise CoreError('VALIDATION_ERROR', 'В автоматическом режиме список фраз должен быть пустым.')
    result = []
    for phrase in keywords:
        if (not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 500
                or any(unicodedata.category(c) == 'Cc' for c in phrase)):
            raise CoreError('VALIDATION_ERROR', 'Укажите непустые фразы до 500 символов без управляющих знаков.')
        phrase = phrase.strip()
        if phrase not in result:
            result.append(phrase)
    if mode == 'manual' and not result:
        raise CoreError('VALIDATION_ERROR', 'Укажите хотя бы одну ключевую фразу.')
    return result


def validate_schedule(schedule) -> dict:
    if (not isinstance(schedule, dict) or set(schedule) != set(DEFAULT_SCHEDULE)
            or type(schedule['enabled']) is not bool or type(schedule['every_days']) is not int
            or schedule['every_days'] < 1
            or schedule['timezone'] != 'Europe/Moscow' or not isinstance(schedule['time'], str)
            or re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', schedule['time']) is None):
        raise CoreError('VALIDATION_ERROR', 'Проверьте расписание: положительное число дней, время ЧЧ:ММ, Europe/Moscow.')
    return dict(schedule)


def next_due(schedule: dict, now: datetime, *, last_auto_day: str | None = None,
             accepted: bool = False) -> str | None:
    if not schedule['enabled']:
        return None
    local = now.astimezone(MOSCOW)
    hour, minute = map(int, schedule['time'].split(':'))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    try:
        if accepted:
            candidate += timedelta(days=schedule['every_days'])
        elif candidate <= local:
            candidate += timedelta(days=1)
        if last_auto_day:
            earliest = datetime.fromisoformat(last_auto_day).replace(tzinfo=MOSCOW, hour=hour, minute=minute)
            earliest += timedelta(days=schedule['every_days'])
            candidate = max(candidate, earliest)
        # Reject unrepresentable intervals before saving an enabled schedule.
        candidate + timedelta(days=schedule['every_days'])
    except OverflowError:
        raise CoreError('VALIDATION_ERROR', 'Интервал расписания выходит за допустимый календарь.') from None
    return candidate.astimezone(timezone.utc).isoformat()


class RadarProfiles:
    def __init__(self, pipeline, repository):
        self.pipeline, self.repository = pipeline, repository

    @staticmethod
    def _source(source):
        if source not in SOURCES:
            raise CoreError('VALIDATION_ERROR', 'Выберите источник профиля радара.')

    def _present(self, source, row) -> dict:
        data = row['data'] if row else default_data()
        adapter = self.pipeline.sources.get(source)
        result = dict(source=source, name=SOURCES[source], run_id=str(row['run_id']) if row and row['run_id'] else None,
                      keyword_mode=data['keyword_mode'], keywords=data['keywords'], schedule=data['schedule'],
                      reanalyze_existing=data.get('reanalyze_existing', False),
                      auto_reanalyze_existing=data.get('auto_reanalyze_existing', False),
                      available=bool(adapter is not None and getattr(adapter, 'key', None)),
                      last_refresh_at=data.get('last_refresh_at'), next_refresh_at=data.get('next_due_at'))
        if data.get('quotas') is not None:
            result['quotas'] = data['quotas']
        return result

    def list(self, actor) -> dict:
        authorize(actor, 'process:read')
        rows = {row['source']: row for row in self.repository.list(actor.data_scope)}
        return {'profiles': [self._present(source, rows.get(source)) for source in SOURCES]}

    def read(self, actor, source) -> dict:
        authorize(actor, 'process:read')
        authorize(actor, 'artifact:read')
        self._source(source)
        row = self.repository.read(actor.data_scope, source)
        return dict(profile=self._present(source, row),
                    run=self.pipeline.read(actor, row['run_id']) if row and row['run_id'] else None)

    def save_keywords(self, actor, source, keyword_mode, keywords) -> dict:
        authorize(actor, 'settings:write')
        authorize(actor, 'process:read')
        self._source(source)
        words = validate_keywords(keyword_mode, keywords)
        row = self.repository.save_keywords(actor.data_scope, source, keyword_mode, words)
        return self._present(source, row)

    def refresh(self, actor, source, command_key, detail_max, schedule=None, *, reanalyze_existing=_UNSET) -> dict:
        return self._refresh(actor, source, command_key, detail_max, schedule, reanalyze_existing=reanalyze_existing)

    def save_schedule(self, actor, source, command_key, schedule, reanalyze_existing, *, now=None) -> dict:
        authorize(actor, 'settings:write')
        authorize(actor, 'process:read')
        self._source(source)
        validate_key(command_key)
        schedule = validate_schedule(schedule)
        if type(reanalyze_existing) is not bool:
            raise CoreError('VALIDATION_ERROR', 'Выберите режим повторного анализа для автообновления.')
        if schedule['enabled']:
            for permission in ('process:start', 'artifact:read', 'settings:read'):
                authorize(actor, permission)
        now = now or datetime.now(timezone.utc)
        def prepare(row):
            return dict(schedule=schedule, auto_reanalyze_existing=reanalyze_existing,
                next_due_at=next_due(schedule, now, last_auto_day=row['data'].get('last_auto_day')),
                schedule_actor=(dict(actor_id=actor.actor_id, permissions=sorted(actor.permissions))
                    if schedule['enabled'] else None))
        row = self.repository.save_schedule(actor, source, command_key,
            dict(schedule=schedule, reanalyze_existing=reanalyze_existing), prepare)
        return self._present(source, row)

    def metrics(self, actor, source, command_key):
        for permission in ('process:start', 'process:read', 'settings:read', 'artifact:read'):
            authorize(actor, permission)
        self._source(source)
        validate_key(command_key)
        request = {'source': source, 'metrics': True}
        previous = self.pipeline.jobs.operation_receipt(actor, command_key)
        if previous is not None:
            if previous['operation'].get('profile_request') != request or previous['operation'].get('kind') != 'profile_metrics':
                raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ относится к другой команде.')
            return dict(previous, run_id=UUID(previous['operation']['run_id']))
        snapshot = self.pipeline.settings.read(actor)
        if snapshot is None:
            raise CoreError('VALIDATION_ERROR', 'Сохраните настройки предприятия и склад.')
        profile = snapshot['profile']
        ref = ArtifactRef(UUID(profile['stock_artifact_id']))
        if self.pipeline.artifacts.metadata(actor, ref)['sha256'] != profile['stock_sha256']:
            raise CoreError('STALE_REVISION', 'Склад отличается от сохранённых настроек.')
        return self.repository.enqueue_metrics(self.pipeline.jobs, actor, source, command_key,
            snapshot['id'], ref, profile['stock_sha256'], request)

    def resume(self, actor, source, command_key, *, expected_job_id=None, rating_version=None):
        for permission in ('process:start', 'process:read', 'artifact:read'):
            authorize(actor, permission)
        self._source(source)
        validate_key(command_key)
        if rating_version is not None and (rating_version != 'card-individual-rating-v1' or expected_job_id is not None):
            raise CoreError('VALIDATION_ERROR', 'Неизвестная версия оценки или неявное обновление версии.')
        if expected_job_id is None:
            row = self.repository.read(actor.data_scope, source)
            if row and row['run_id']:
                prior_metrics = self.pipeline.jobs.operation_receipt(actor, command_key)
                if prior_metrics is not None and prior_metrics['operation'].get('kind') == 'profile_metrics':
                    if prior_metrics['operation'].get('run_id') != str(row['run_id']):
                        raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ относится к другому профилю.')
                    return dict(prior_metrics, run_id=row['run_id'])
                raw = self.pipeline.repository.read(actor.data_scope, row['run_id'])
                last_metrics = raw['data'].get('last_metrics_job_id') if raw else None
                metrics_job = self.pipeline.jobs.read(actor.data_scope, UUID(last_metrics)) if last_metrics else None
                if metrics_job and metrics_job['state'] == 'failed':
                    return self.repository.resume_metrics(self.pipeline.jobs, actor, source, command_key, metrics_job)
                reference = {'rating_version': 'card-individual-rating-v1'}
                prior = self.pipeline.jobs.operation_receipt(actor, command_key)
                if prior is not None and prior['operation'].get('kind') == 'rating':
                    if (prior['operation'].get('run_id') != str(row['run_id'])
                            or prior['operation'].get('reference') != reference):
                        raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ команды относится к другой оценке.')
                    return dict(prior, run_id=row['run_id'])
                run = self.pipeline.repository.read(actor.data_scope, row['run_id'])
                previous_id = run['data'].get('last_individual_rating_job_id') if run else None
                previous = self.pipeline.jobs.read(actor.data_scope, UUID(previous_id)) if previous_id else None
                if (run and run['data']['state'] == 'complete' and previous is not None
                        and previous['state'] == 'failed'):
                    receipt = self.pipeline.operation(actor, row['run_id'], command_key, 'rating', reference=reference)
                    return dict(receipt, run_id=row['run_id'])
        return self.repository.resume_refresh(self.pipeline.jobs, actor, source, command_key,
            expected_job_id=expected_job_id, **({'rating_version': rating_version} if rating_version is not None else {}))

    def recover_interrupted(self):
        count = 0
        for row in self.repository.recoverable():
            # Continue the already-authorized immutable operation, not a new search scope.
            actor = ActorContext(row['actor_id'], row['data_scope'],
                frozenset({'process:start', 'process:read', 'artifact:read'}))
            try:
                if row.get('kind') == 'profile_metrics':
                    receipt = self.repository.resume_metrics(self.pipeline.jobs, actor, row['source'],
                        'metrics-recover:' + str(row['job_id']), row, expected_job_id=row['job_id'])
                else:
                    receipt = self.resume(actor, row['source'], 'profile-recover:' + str(row['job_id']),
                                          expected_job_id=row['job_id'])
                count += int(receipt.get('_accepted', False))
            except CoreError as error:
                if error.code not in {'PROCESS_ACTIVE', 'CONFLICT'}:
                    raise
        return count

    def _refresh(self, actor, source, command_key, detail_max, schedule=None, *, automatic=False, now=None,
                 reanalyze_existing=_UNSET) -> dict:
        for permission in ('process:start', 'artifact:read', 'settings:read'):
            authorize(actor, permission)
        self._source(source)
        validate_key(command_key)
        if detail_max is not None and (type(detail_max) is not int or not 0 <= detail_max <= 2_147_483_647):
            raise CoreError('VALIDATION_ERROR', 'Укажите целый неотрицательный лимит подробностей или null.')
        if schedule is not None:
            schedule = validate_schedule(schedule)
        if reanalyze_existing is not _UNSET and type(reanalyze_existing) is not bool:
            raise CoreError('VALIDATION_ERROR', 'Выберите, анализировать ли сохранённые подробные карточки.')
        request = dict(source=source, detail_max=detail_max, schedule=schedule)
        if reanalyze_existing is not _UNSET:
            request['reanalyze_existing'] = reanalyze_existing
        # A command is tied to its original inputs, even after settings or stock change.
        replay = self.repository.replay(actor, command_key, request)
        if replay is not None:
            return dict(replay, _accepted=False)
        now = now or datetime.now(timezone.utc)

        def prepare(row, run_id):
            data = row['data']
            if automatic:
                due = data.get('next_due_at')
                if (not data['schedule']['enabled'] or not due or datetime.fromisoformat(due) > now
                        or data.get('last_auto_day') == now.astimezone(MOSCOW).date().isoformat()):
                    raise CoreError('SCHEDULE_NOT_DUE', 'Время автоматического обновления ещё не наступило.')
            adapter = self.pipeline.sources.get(source)
            if adapter is None or not getattr(adapter, 'key', None):
                raise CoreError('VALIDATION_ERROR', 'Выбранный источник не настроен на сервере.')
            snapshot = self.pipeline.settings.read(actor)
            if snapshot is None:
                raise CoreError('VALIDATION_ERROR', 'Сохраните профиль предприятия и склад перед обновлением.')
            company = snapshot['profile']
            if not search_phrases(company):
                raise CoreError('VALIDATION_ERROR', 'Включите хотя бы один продукт в настройках предприятия.')
            ref = ArtifactRef(UUID(company['stock_artifact_id']))
            if self.pipeline.artifacts.metadata(actor, ref)['sha256'] != company['stock_sha256']:
                raise CoreError('STALE_REVISION', 'Склад отличается от сохранённого профиля предприятия.')
            words = data['keywords'] if data['keyword_mode'] == 'manual' else []
            inputs = dict(mode=data['keyword_mode'], keywords=words)
            if data['keyword_mode'] == 'auto':
                inputs['products'] = company.get('products', [])
                inputs['experimental_products'] = company.get('experimental_products', [])
                inputs['include_experimental'] = company.get('include_experimental', False)
            fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False,
                                                   separators=(',', ':')).encode()).hexdigest()
            operation = dict(kind='profile_refresh', profile_source=source, source=source, run_id=str(run_id),
                             refresh_version='incremental-profile-v3',
                             reanalyze_existing=(data.get('auto_reanalyze_existing', False) if automatic else
                                 data.get('reanalyze_existing', False) if reanalyze_existing is _UNSET else reanalyze_existing),
                             name=SOURCES[source], settings_snapshot_id=str(snapshot['id']),
                             keyword_mode=data['keyword_mode'], keywords=words, detail_max=detail_max,
                             search_fingerprint=fingerprint, profile_request=request,
                             budgets=dict(search=None, detail=detail_max, model=None))
            updates = {}
            if reanalyze_existing is not _UNSET and not automatic:
                updates['reanalyze_existing'] = reanalyze_existing
            if automatic:
                updates.update(last_auto_day=now.astimezone(MOSCOW).date().isoformat(),
                               next_due_at=next_due(data['schedule'], now, accepted=True))
            return dict(operation=operation, snapshot_id=UUID(str(snapshot['id'])), ref=ref,
                        sha256=company['stock_sha256'], profile_updates=updates)

        return self.repository.enqueue_refresh(self.pipeline.jobs, actor, source, command_key, request, prepare)

    def enqueue_due(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('Schedule ticks require an aware UTC timestamp')
        now = now.astimezone(timezone.utc)
        count = self.recover_interrupted()
        for row in self.repository.due(now):
            owner = row['data'].get('schedule_actor') or {}
            actor = ActorContext(owner.get('actor_id', 'radar-scheduler'), row['data_scope'],
                                 frozenset(owner.get('permissions', [])))
            due_day = datetime.fromisoformat(row['data']['next_due_at']).astimezone(MOSCOW).date().isoformat()
            key = f'profile-auto:{row["source"]}:{due_day}'
            try:
                job = self._refresh(actor, row['source'], key, None, automatic=True, now=now)
                count += int(job.get('_accepted', False))
            except CoreError as error:
                if error.code in {'PROCESS_ACTIVE', 'SCHEDULE_NOT_DUE'}:
                    continue
                self.repository.schedule_failed(row['data_scope'], row['source'], row['data']['next_due_at'],
                    {'code': error.code, 'message': error.message}, now,
                    next_due(row['data']['schedule'], now, accepted=True))
        return count
