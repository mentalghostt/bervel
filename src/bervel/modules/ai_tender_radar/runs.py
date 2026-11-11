"""Profile-driven scenarios, executed by the existing AI worker job queue."""
from copy import copy, deepcopy
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from functools import lru_cache
from math import isfinite
from time import time
from typing import Protocol
from uuid import UUID, uuid4

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef
from bervel.core.services import authorize, validate_key
from bervel.modules.company_settings import search_phrases, stock_for_profile
from bervel.modules.ai_tender_radar.brief_ranking import AI_SELECTION_VERSION
from bervel.modules.ai_tender_radar import brief_ranking as model_retry
from bervel.modules.ai_tender_radar.calculation import calculate_analysis
from bervel.modules.ai_tender_radar.completeness import check_completeness
from bervel.modules.ai_tender_radar.comparative_rating import (
    apply_rating, build_snapshot, initial_result, matching_report, rate_snapshot,
)
from bervel.modules.ai_tender_radar.documents import LEGACY_PROMPT_VERSION as LEGACY_DOCUMENT_PROMPT_VERSION
from bervel.modules.ai_tender_radar.documents import PROMPT_VERSION as DOCUMENT_PROMPT_VERSION
from bervel.modules.ai_tender_radar.documents import SCOPED_PROMPT_VERSION, enrich_tender
from bervel.modules.ai_tender_radar.matching import match_positions
from bervel.modules.ai_tender_radar.prefilter import CANDIDATE_PRIORITY_VERSION, LOT_COVERAGE_SELECTION_VERSION
from bervel.modules.ai_tender_radar.models import USER_STOPPED
from bervel.modules.ai_tender_radar.production import BASELINE_PRODUCTION_RULE_VERSION, PRODUCTION_RULE_VERSION, evaluate_fulfillment
from bervel.modules.ai_tender_radar.review import ANALYTICAL_PROMPT_VERSION, PROMPT_VERSION as REVIEW_PROMPT_VERSION
from bervel.modules.ai_tender_radar.presentation import present_card, present_unavailable, present_refresh_warnings
from bervel.modules.ai_tender_radar.result_metrics import result_metrics
from bervel.modules.ai_tender_radar.profile_policy import profile_metrics
from bervel.modules.ai_tender_radar.individual_rating import (
    PROMPT_VERSION as INDIVIDUAL_RATING_VERSION, build_card_rating_input, rate_card,
)
from bervel.modules.ai_tender_radar.metrics_refresh import project_metrics, refresh_metrics_cards
from bervel.modules.ai_tender_radar.scoring import opportunity_assessment, score_assessment
from bervel.modules.ai_tender_radar.selection import collect_candidates, rank_candidates, require_simulator, rerank_candidates, search_queries
from bervel.modules.ai_tender_radar.source_contract import PURCHASE_STATUS
from bervel.modules.company_settings.policy import DEFAULT_SCORING_POLICY


def _stage_versions(selection: str, *, legacy: bool = False) -> dict:
    priority = selection in {CANDIDATE_PRIORITY_VERSION, AI_SELECTION_VERSION}
    review_version = ANALYTICAL_PROMPT_VERSION if priority or selection == LOT_COVERAGE_SELECTION_VERSION else REVIEW_PROMPT_VERSION
    return {'selection': selection, 'normalization': 'kontur-v2', 'matching': 'verified-sku-2026-11-06',
            'documents': LEGACY_DOCUMENT_PROMPT_VERSION if legacy else SCOPED_PROMPT_VERSION if priority else DOCUMENT_PROMPT_VERSION,
            'production': 2 if legacy else PRODUCTION_RULE_VERSION,
            'score': 'score-v1' if legacy else DEFAULT_SCORING_POLICY['version'],
            'review': 'card-review-v1' if legacy else review_version, 'audit': 'run-audit-v1'}

def _run_data_with_job_status(data: dict, state: str | None, error: dict | None) -> dict:
    stopped = (error or {}).get('code') == USER_STOPPED
    if state == 'failed':
        return dict(data, state='failed', error=error, **({'stop_reason': USER_STOPPED} if stopped else {}))
    if state == 'running' and data.get('state') == 'complete':
        data = dict(data, state='running', phase='rating')
    if state == 'running' and stopped:
        return dict(data, state='running', stop_requested=True)
    return data


class RunRepository(Protocol):
    def enqueue_operation(self, jobs, actor, key, ref, phrase, sha256, operation, *, initial=None, run_id=None): ...
    def ensure(self, scope, run_id, snapshot_id, data): ...
    def read(self, scope, run_id): ...
    def list(self, scope, limit=30, offset=0): ...
    def delete(self, scope: str, run_id: UUID) -> None: ...
    def delete_legacy(self, scope: str) -> None: ...
    def stop(self, scope: str, run_id: UUID) -> None: ...
    def update(self, scope, run_id, data): ...
    def cards(self, run_id): ...
    def add_card(self, run_id, candidate_id, data): ...
    def finalize_card(self, run_id, candidate_id, data): ...
    def retry_failed_card(self, run_id, candidate_id, job_id): ...
    def report(self, job_id, run_id, data): ...
    def read_reports(self, run_id): ...
    def reserve_rating(self, job_id, run_id, result): ...
    def archive_page(self, run_id, page): ...
    def source_pages(self, run_id): ...
    def prepare_card(self, run_id, candidate_id, tender): ...
    def legacy_entry(self, scope): ...
    def legacy_cards(self, scope, limit=30, offset=0): ...


class _BudgetedModel:
    def __init__(self, model, state, maximum, save):
        self.model, self.state, self.maximum, self.save = model, state, maximum, save

    def generate(self, *args, **kwargs):
        if self.state.get('model_attempts', 0) >= self.maximum:
            raise CoreError('MODEL_QUOTA_EXCEEDED', 'Исчерпан бюджет модельных вызовов прогона.')
        self.state['model_attempts'] = self.state.get('model_attempts', 0) + 1
        self.save()
        return self.model.generate(*args, **kwargs)


class RadarRuns:
    def __init__(self, repository: RunRepository, jobs, settings, artifacts, parse_stock, source, model, *, parse_archive=None, sources=None):
        self.repository, self.jobs, self.settings = repository, jobs, settings
        self.artifacts, self.parse_stock, self.source, self.model = artifacts, parse_stock, source, model
        self.parse_archive = parse_archive
        self.profiles = None
        self.sources=sources or ({'test_api':source} if getattr(source,'simulated',False) else {'kontur_api':source})

    @staticmethod
    def _authorize(actor):
        authorize(actor, 'process:read')
        authorize(actor, 'artifact:read')

    def enqueue(self, actor, snapshot_id, command_key, *, search_max=40, detail_max=None, model_max=1000,
                name=None, source=None, source_artifact_id=None, purchase_stages=None):
        authorize(actor, 'process:start')
        authorize(actor, 'artifact:read')
        validate_key(command_key)
        if name is not None or source is not None or purchase_stages is not None:
            return self._enqueue_named(actor,snapshot_id,command_key,name,source,source_artifact_id,detail_max,purchase_stages)
        detail_max = 200 if detail_max is None else detail_max
        require_simulator(self.source)
        snapshot = self.settings.read_snapshot(actor.data_scope, snapshot_id)
        profile = snapshot['profile']
        operation = {'kind': 'selection', 'settings_snapshot_id': str(snapshot_id),
                     'budgets': {'search': search_max, 'detail': detail_max, 'model': model_max}}
        if not (1 <= search_max <= 200 and 1 <= detail_max <= 200 and 0 <= model_max <= 1000):
            raise CoreError('VALIDATION_ERROR', 'Проверьте бюджеты: поиск и карточки 1–200, модель 0–1000.')
        if not search_phrases(profile):
            raise CoreError('VALIDATION_ERROR', 'Включите хотя бы один продукт в настройках предприятия.')
        ref = ArtifactRef(UUID(profile['stock_artifact_id']))
        metadata = self.artifacts.metadata(actor, ref)
        if metadata['sha256'] != profile['stock_sha256']:
            raise CoreError('STALE_REVISION', 'Склад отличается от сохранённого снимка настроек.')
        return self.repository.enqueue_operation(self.jobs, actor, command_key, ref, 'Профиль предприятия',
            profile['stock_sha256'], operation, initial={'state': 'queued', 'phase': 'queued', 'checkpoint': {},
                'budgets': operation['budgets'], 'environment': 'simulation',
                'stage_versions': _stage_versions('selection-v1')})

    def _enqueue_named(self,actor,snapshot_id,command_key,name,source,source_artifact_id,detail_max,purchase_stages):
        if not isinstance(name,str) or not 1<=len(name.strip())<=200 or any(ord(c)<32 for c in name):
            raise CoreError('VALIDATION_ERROR','Введите название прогона до 200 символов.')
        if source not in {'test_api','kontur_api','file'}:
            raise CoreError('VALIDATION_ERROR','Выберите источник закупок.')
        if purchase_stages is not None:
            if source == 'file' or not isinstance(purchase_stages, list) or not purchase_stages or any(
                    not isinstance(stage, str) or stage not in PURCHASE_STATUS for stage in purchase_stages):
                raise CoreError('VALIDATION_ERROR', 'Выберите хотя бы один допустимый этап закупки для поиска по API.')
            purchase_stages = [stage for stage in PURCHASE_STATUS if stage in purchase_stages]
        if detail_max is not None and (type(detail_max) is not int or not 1 <= detail_max <= 2_147_483_647):
            raise CoreError('VALIDATION_ERROR', 'Укажите положительный лимит подробных карточек.')
        if source=='file' and source_artifact_id is None:
            raise CoreError('VALIDATION_ERROR','Выберите JSON-файл закупок.')
        if source!='file':
            adapter=self.sources.get(source)
            if adapter is None or not getattr(adapter,'key',None):
                raise CoreError('VALIDATION_ERROR','Выбранный источник не настроен на сервере.')
            if source=='test_api':
                require_simulator(adapter)
        elif source_artifact_id is not None:
            metadata=self.artifacts.metadata(actor,ArtifactRef(source_artifact_id))
            if not metadata['filename'].casefold().endswith('.json'):
                raise CoreError('VALIDATION_ERROR','Для источника закупок выберите JSON-файл.')
        snapshot=self.settings.read_snapshot(actor.data_scope,snapshot_id)
        profile=snapshot['profile']
        if not search_phrases(profile):
            raise CoreError('VALIDATION_ERROR','Включите продукцию в настройках предприятия.')
        ref=ArtifactRef(UUID(profile['stock_artifact_id']))
        if self.artifacts.metadata(actor,ref)['sha256']!=profile['stock_sha256']:
            raise CoreError('STALE_REVISION','Склад отличается от сохранённого профиля.')
        operation={'kind':'selection','settings_snapshot_id':str(snapshot_id),'name':name.strip(),'source':source,
                   'source_artifact_id':str(source_artifact_id) if source_artifact_id is not None else None,
                   'selection_version':3,'budgets':{'search':None,'detail':detail_max,'model':None}}
        if source=='file':
            operation['source_sha256']=metadata['sha256']
        if purchase_stages is not None:
            operation['purchase_stages'] = purchase_stages
        return self.repository.enqueue_operation(self.jobs,actor,command_key,ref,'Профиль предприятия',
            profile['stock_sha256'],operation,initial={'state':'queued','phase':'queued','checkpoint':{},
                'name':name.strip(),'source':source,'source_artifact_id':operation['source_artifact_id'],
                **({'source_sha256':operation['source_sha256']} if source=='file' else {}),
                **({'purchase_stages': purchase_stages} if purchase_stages is not None else {}),
                'selection_version':3,'budgets':operation['budgets'],
                'stage_versions':_stage_versions(CANDIDATE_PRIORITY_VERSION if source == 'file' else AI_SELECTION_VERSION),
                'environment':'simulation' if source=='test_api' else 'file' if source=='file' else 'kontur'})

    def read(self, actor, run_id):
        self._authorize(actor)
        row = self.repository.read(actor.data_scope, run_id)
        if row is None:
            raise CoreError('NOT_FOUND', 'Прогон не найден в этой области доступа.')
        now = datetime.now(timezone.utc)
        profile_mode = bool(row['data'].get('profile_source'))

        @lru_cache(maxsize=1)
        def historical_inputs():
            try:
                snapshot = self.settings.read_snapshot(actor.data_scope, row['settings_snapshot_id'])
            except CoreError as error:
                if error.code != 'NOT_FOUND':
                    raise
                return None
            version = row['data'].get('stage_versions', {}).get('production', 2)
            profile = snapshot['profile']
            try:
                artifact = self.artifacts.read(actor, ArtifactRef(UUID(profile['stock_artifact_id'])))
            except CoreError as error:
                if error.code not in {'NOT_FOUND', 'TRANSIENT_DEPENDENCY_ERROR'}:
                    raise
                return None
            if artifact.metadata['sha256'] != profile['stock_sha256']:
                raise CoreError('STALE_REVISION', 'Склад отличается от исторического снимка настроек.')
            stock = stock_for_profile(self.parse_stock(artifact.content), profile, legacy_links=version == 1,
                                      expose_experimental_sku=version >= BASELINE_PRODUCTION_RULE_VERSION)
            return profile, stock

        @lru_cache(maxsize=1)
        def historical_stock_rows():
            inputs = historical_inputs()
            return {item['sku']: item for item in inputs[1]['rows']} if inputs else {}

        row['cards'] = []
        for entry in self.repository.cards(run_id):
            card = entry['data']
            if profile_mode:
                card = present_refresh_warnings(card, row['data'].get('refresh_checkpoint') or {})
            inputs = historical_inputs() if card.get('analysis') and not profile_mode else None
            if card.get('analysis') and not profile_mode:
                card = present_card(card, *inputs) if inputs else present_unavailable(card)
            elif not card.get('analysis'):
                card = dict(card, opportunity=opportunity_assessment(card))
            metrics = profile_metrics(card, now=now) if profile_mode else result_metrics(
                card, now=now, historical_stock_rows=historical_stock_rows)
            row['cards'].append(dict(card, metrics=metrics))
        loaded = {card['candidate_id'] for card in row['cards']}
        row['candidates'] = [dict(id=c['id'], brief=c['brief'], state=c.get('state', 'not_loaded'),
            metrics=result_metrics({'brief': c['brief'], 'purchase_stages': row['data'].get('purchase_stages')}, now=now),
            reasons=c.get('reasons', ['DETAIL_BUDGET_NOT_SELECTED']))
            for c in row['data'].get('ranked_candidates', row['data'].get('checkpoint', {}).get('candidates', [])) if c['id'] not in loaded]
        row['search_pages'] = [dict(page_id=p['page_id'], phrase=p['data']['phrase'], page=p['data']['page'],
            total_count=(p['data'].get('response') or {}).get('TotalCount')
                if isinstance(p['data'].get('response'), dict) else None)
            for p in self.repository.source_pages(run_id)]
        saved_reports = self.repository.read_reports(run_id)
        row['reports'] = [dict(job_id=str(entry['job_id']), **entry['data']) for entry in saved_reports]
        for card in row['cards']:
            for report in row['reports']:
                if (not profile_mode and report['kind'] == 'review' and report.get('analysis_id') == card['analysis_id']
                        and 'ORIGINAL_STOCK_UNAVAILABLE' not in (card.get('projection') or {}).get('reasons', [])
                        and report.get('projection_version') == (card.get('projection') or {}).get('version')
                        and report.get('result', {}).get('state') == 'complete'):
                    card['review'] = report['result']
                    card['projection']['review_stale'] = False
        rated_cards = [card for card in row['cards'] if card.get('details_loaded') and card.get('analysis')
                       and card['metrics']['actuality'] == 'active'] if profile_mode else row['cards']
        rating_version = row['data'].get('individual_rating_version') or row['data'].get('stage_versions', {}).get('rating')
        apply_rating(rated_cards, saved_reports, rating_version=rating_version)
        if profile_mode:
            rated_ids = {card['analysis_id'] for card in rated_cards}
            for card in row['cards']:
                if card['analysis_id'] not in rated_ids:
                    card['rating'] = {'status': 'stale' if card.get('details_loaded') else 'pending', 'score': None}
                    if rating_version == INDIVIDUAL_RATING_VERSION:
                        card['rating']['prompt_version'] = INDIVIDUAL_RATING_VERSION
        current = self.jobs.read(actor.data_scope, UUID(row['data']['current_job_id']))
        rating_execution = current
        if current is not None:
            row['data'] = _run_data_with_job_status(row['data'], current['state'], current['error'])
        rating_job_id = row['data'].get('individual_rating_job_id') or row['data'].get('last_individual_rating_job_id')
        if rating_job_id:
            rating_job = self.jobs.read(actor.data_scope, UUID(rating_job_id))
            if rating_job is not None:
                rating_execution = rating_job
            if rating_job is not None and rating_job['state'] in {'queued', 'running'}:
                eligible = [card for card in row['cards'] if card.get('details_loaded') and card.get('analysis')
                    and not card.get('analysis_stale') and not (card['analysis'] or {}).get('model_error')
                    and card['metrics']['actuality'] == 'active']
                row['data'] = dict(row['data'], state=rating_job['state'], phase='rating',
                    rating_completed=sum(bool(card.get('individual_rating')) and card.get('rating', {}).get('status') == 'complete'
                                         for card in eligible), rating_total=len(eligible))
            elif rating_job is not None and rating_job['state'] == 'failed':
                row['data'] = dict(_run_data_with_job_status(row['data'], 'failed', rating_job.get('error')),
                                   phase='rating', resume_kind='individual_rating')
        if rating_execution is not None and rating_execution['state'] in {'loaded', 'failed', 'no_match'}:
            for card in row['cards']:
                if card.get('individual_rating') and card.get('rating', {}).get('status') == 'running':
                    card['rating'] = dict(card['rating'], status='unknown', score=None)
        row['cards'] = [project_metrics(card) for card in row['cards']]
        metrics_id = row['data'].get('metrics_job_id') or row['data'].get('last_metrics_job_id')
        if metrics_id:
            metrics_job = self.jobs.read(actor.data_scope, UUID(metrics_id))
            if metrics_job:
                row['data'] = dict(row['data'], metrics_job={'job_id': metrics_id, 'state': metrics_job['state'],
                                                            'error': metrics_job.get('error')})
                if metrics_job['state'] in {'queued', 'running', 'failed'}:
                    row['data'] = dict(row['data'], state=metrics_job['state'], phase='metrics',
                        checkpoint=dict(row['data'].get('checkpoint') or {}, **row['data'].get('metrics_checkpoint', {})),
                        **({'error': metrics_job.get('error'), 'resume_kind': 'metrics'} if metrics_job['state'] == 'failed' else {}))
        return row

    def list(self, actor, limit=30, offset=0):
        self._authorize(actor)
        page = self.repository.list(actor.data_scope, limit, offset)
        for run in page['runs']:
            run['data'] = _run_data_with_job_status(run['data'], run.pop('current_job_state'), run.pop('current_job_error'))
        return page

    def delete(self, actor: ActorContext, run_id: UUID) -> None:
        self._authorize(actor)
        authorize(actor, 'process:start')
        self.repository.delete(actor.data_scope, run_id)

    def delete_legacy(self, actor: ActorContext) -> None:
        self._authorize(actor)
        authorize(actor, 'process:start')
        self.repository.delete_legacy(actor.data_scope)

    def stop(self, actor: ActorContext, run_id: UUID) -> None:
        self._authorize(actor)
        authorize(actor, 'process:start')
        self.repository.stop(actor.data_scope, run_id)

    def history(self, actor, limit=30, offset=0):
        self._authorize(actor)
        legacy=self.repository.legacy_entry(actor.data_scope)
        entries=[legacy] if legacy is not None and offset==0 else []
        run_offset=max(0,offset-(1 if legacy else 0))
        page=self.list(actor,limit-len(entries),run_offset)
        for run in page['runs']:
            data=run['data']
            entries.append(dict(id=str(run['run_id']),kind='run',title=data.get('name') or 'Анализ тендеров',
                created_at=run['created_at'],state=data['state'],card_count=run['card_count'],
                **({'stop_reason': USER_STOPPED} if data['state'] == 'failed' and data.get('stop_reason') == USER_STOPPED else {}),
                **({'stop_requested': True} if data.get('stop_requested') else {})))
        return {'entries':entries,'next_offset':offset+len(entries) if page['next_offset'] is not None else None}

    def legacy(self, actor, limit=30, offset=0):
        self._authorize(actor)
        result=self.repository.legacy_cards(actor.data_scope,limit,offset)
        if result is None:
            raise CoreError('NOT_FOUND','Архив прежних результатов не найден в этой области доступа.')
        return result

    def operation(self, actor, run_id, command_key, kind, *, analysis_id=None, question=None, reference=None, max_cards=None,
                  settings_snapshot_id=None):
        authorize(actor, 'process:start')
        run = self.read(actor, run_id)
        validate_key(command_key)
        if kind not in {'resume', 'question', 'review', 'audit', 'reanalyze', 'rating'}:
            raise CoreError('VALIDATION_ERROR', 'Неизвестная операция прогона.')
        if settings_snapshot_id is not None and kind != 'reanalyze':
            raise CoreError('VALIDATION_ERROR', 'Смена профиля доступна только для нового пересчёта.')
        if kind == 'reanalyze':
            card = next((c for c in run['cards'] if c['analysis_id'] == str(analysis_id)), None)
            if card is None or not card.get('tender'):
                raise CoreError('NOT_FOUND', 'Сохранённая карточка для пересчёта не найдена.')
            try:
                snapshot_id = UUID(settings_snapshot_id) if settings_snapshot_id else run['settings_snapshot_id']
            except ValueError:
                raise CoreError('VALIDATION_ERROR', 'Некорректный ID профиля.') from None
            profile = self.settings.read_snapshot(actor.data_scope, snapshot_id)['profile']
            payload = {'kind': 'reanalyze', 'source_run_id': str(run_id), 'source_analysis_id': str(analysis_id),
                       'settings_snapshot_id': str(snapshot_id)}
            existing = self.jobs.replay_operation(actor, command_key, payload)
            if existing is not None:
                return existing
            selection = [{'id': card['candidate_id'], 'brief': card['brief'], 'state': 'candidate'}]
            initial = {'state': 'queued', 'phase': 'queued', 'source_kind': 'saved_card',
                'name':('Пересчёт: '+run['data'].get('name','Анализ тендеров'))[:200],
                **({'source':run['data']['source']} if run['data'].get('source') else {}),
                'source_run_id': str(run_id), 'source_analysis_id': str(analysis_id), 'selection': selection,
                'stage_versions': _stage_versions('selection-v1'),
                'budgets': {'search': 0, 'detail': 0, 'model': 300}, 'effective_budgets': {'search': 0, 'detail': 0},
                'checkpoint': {'candidates': selection, 'stop_reason': 'saved_card', 'search_attempts': 0}}
            return self.repository.enqueue_operation(self.jobs, actor, command_key, ArtifactRef(UUID(profile['stock_artifact_id'])),
                'Пересчёт сохранённой карточки', profile['stock_sha256'], payload, initial=initial, run_id=run_id)
        if kind == 'question':
            if (not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000
                    or any(ord(char)<32 and char not in '\n\r\t' for char in question)):
                raise CoreError('VALIDATION_ERROR', 'Введите вопрос от 1 до 2000 символов.')
            if not any(card['analysis_id'] == str(analysis_id) for card in run['cards']):
                raise CoreError('NOT_FOUND', 'Карточка не принадлежит этому прогону.')
        if kind == 'review' and not any(card['analysis_id'] == str(analysis_id) and card.get('analysis')
                                       for card in run['cards']):
            raise CoreError('NOT_FOUND', 'Расчёт карточки для ревью не найден в этом прогоне.')
        if kind in {'question', 'rating'} and run['data'].get('profile_source'):
            receipt = self.jobs.operation_receipt(actor, command_key)
            if receipt is not None:
                public = {'kind': kind, 'run_id': str(run_id), 'analysis_id': str(analysis_id) if analysis_id else None,
                          'question': question, 'reference': reference, 'max_cards': max_cards}
                if {key: (receipt.get('operation') or {}).get(key) for key in public} != public:
                    raise CoreError('IDEMPOTENCY_CONFLICT', 'Ключ команды уже использован с другими параметрами.')
                return receipt
        if kind == 'rating' and (run['data']['state'] not in {'complete', 'failed'}
                or any(card['state'] == 'analyzing' for card in run['cards'])):
            raise CoreError('CONFLICT', 'Сравнительная оценка доступна после завершения обработки карточек.')
        if kind == 'rating' and isinstance(reference, dict) and 'rating_version' in reference:
            if reference != {'rating_version': INDIVIDUAL_RATING_VERSION}:
                raise CoreError('VALIDATION_ERROR', 'Неизвестная версия отдельной оценки карточек.')
            underlying = self.repository.read(actor.data_scope, run_id)
            if (not run['data'].get('profile_source') or not underlying
                    or underlying['data']['state'] != 'complete'):
                raise CoreError('CONFLICT', 'Для незавершённого профиля используйте явное продолжение с версией оценки.')
        snapshot_id = run['settings_snapshot_id']
        profile = self.settings.read_snapshot(actor.data_scope, snapshot_id)['profile']
        payload = {'kind': kind, 'run_id': str(run_id), 'settings_snapshot_id': str(snapshot_id),
                   'analysis_id': str(analysis_id) if analysis_id else None, 'question': question,
                   'reference': reference, 'max_cards': max_cards}
        existing = self.jobs.replay_operation(actor, command_key, payload)
        if existing is not None:
            return existing
        if kind == 'question' and any(card['analysis_id'] == str(analysis_id) and card['state'] == 'analyzing'
                                      for card in run['cards']):
            raise CoreError('CONFLICT', 'Дождитесь завершения анализа этой карточки.')
        if kind == 'resume' and run['data']['state'] != 'failed':
            raise CoreError('CONFLICT', 'Продолжение доступно после завершившейся ошибки.')
        return self.repository.enqueue_operation(self.jobs, actor, command_key, ArtifactRef(UUID(profile['stock_artifact_id'])),
            'Операция прогона', profile['stock_sha256'], payload, run_id=run_id)

    def rate_run(self, job, run, profile, stock, *, model=None):
        """Shared manual/automatic command. A durable reservation prevents replay."""
        if (job['operation'].get('reference') or {}).get('rating_version') == INDIVIDUAL_RATING_VERSION:
            return self.rate_saved_cards(job, run, profile, stock)
        run_id = UUID(str(run.get('run_id') or job['operation'].get('run_id') or job['job_id']))
        cards = []
        rows = {item['sku']: item for item in stock['rows']}
        for entry in self.repository.cards(run_id):
            card = entry['data']
            if run['data'].get('profile_source') and not (card.get('details_loaded') and card.get('analysis')):
                continue
            card = present_card(card, profile, stock) if card.get('analysis') else dict(card, opportunity=opportunity_assessment(card))
            metrics = profile_metrics(card) if run['data'].get('profile_source') else result_metrics(card, historical_stock_rows=lambda: rows)
            if run['data'].get('profile_source') and metrics['actuality'] != 'active':
                continue
            cards.append(dict(card, metrics=metrics))
        snapshot = build_snapshot(cards)
        if run['data'].get('profile_source'):
            origin = job['operation'].get('refresh_id') or str(job['job_id'])
            saved_reports = self.repository.read_reports(run_id)
            lineage = [entry for entry in saved_reports if entry['data'].get('kind') == 'rating'
                and entry['data'].get('result', {}).get('refresh_id') == origin]
            prior = lineage[-1] if lineage else matching_report(snapshot, saved_reports)
            if not cards and not lineage:
                return {'status': 'complete', 'ratings': [], 'cohort_size': 0}
            checkpoint = None
            if prior is not None:
                previous = prior['data']['result']
                if previous.get('status') == 'complete':
                    reused = dict(deepcopy(previous), refresh_id=origin)
                    self.repository.report(job['job_id'], run_id,
                        {'kind': 'rating', 'source_job_id': str(prior['job_id']), 'result': reused})
                    return reused
                if lineage and previous.get('snapshot'):
                    snapshot = deepcopy(previous['snapshot'])
                    checkpoint = previous
            request_id = checkpoint['request_id'] if checkpoint else origin
            def save_rating(value):
                self.repository.report(job['job_id'], run_id,
                    {'kind': 'rating', 'result': dict(value, refresh_id=origin)})
            return rate_snapshot(snapshot, model or self.model, request_id,
                checkpoint=checkpoint, heartbeat=lambda: self.jobs.heartbeat(job['job_id']), save=save_rating)
        result = initial_result(snapshot, str(job['job_id']))
        reservation = self.repository.reserve_rating(job['job_id'], run_id, result)
        if not reservation['created']:
            reused = deepcopy(reservation['data']['result'])
            if reused['status'] == 'running':
                reused.update(status='unknown', error={'code': 'RATING_ALREADY_STARTED',
                    'message': 'Для этого набора уже начат запрос оценки. Новая попытка автоматически не отправляется.'})
            self.repository.report(job['job_id'], run_id, {'kind': 'rating',
                'source_job_id': str(reservation['job_id']), 'result': reused})
            return reused
        return rate_snapshot(snapshot, model or self.model, str(job['job_id']),
            heartbeat=lambda: self.jobs.heartbeat(job['job_id']),
            save=lambda value: self.repository.report(job['job_id'], run_id, {'kind': 'rating', 'result': value}))

    def rate_saved_cards(self, job, run, profile, stock):
        """Explicit backfill owns only its rating marker, never the profile refresh owner."""
        run_id = UUID(str(run['run_id']))
        repository = self.profiles.repository.execution(self.repository, job['data_scope'], run_id,
            job['job_id'], owner_field='individual_rating_job_id')
        heartbeat = lambda: self.jobs.heartbeat(job['job_id'])
        inputs = {str(run['settings_snapshot_id']): (profile, stock)}
        saved_cards = repository.cards(run_id)
        cards = [row['data'] for row in saved_cards if row['data'].get('details_loaded')
            and row['data'].get('analysis') and not row['data'].get('analysis_stale')
            and not (row['data']['analysis'] or {}).get('model_error')
            and profile_metrics(row['data'])['actuality'] == 'active']
        for card in cards:
            snapshot_id = card.get('analysis_settings_snapshot_id') or str(run['settings_snapshot_id'])
            if snapshot_id not in inputs:
                historical = self.settings.read_snapshot(job['data_scope'], UUID(snapshot_id))['profile']
                actor = ActorContext(job['actor_id'], job['data_scope'], frozenset({'artifact:read'}))
                artifact = self.artifacts.read(actor, ArtifactRef(UUID(historical['stock_artifact_id'])))
                if artifact.metadata['sha256'] != historical['stock_sha256']:
                    raise CoreError('STALE_REVISION', 'Склад отличается от исходного анализа карточки.')
                inputs[snapshot_id] = (historical, stock_for_profile(self.parse_stock(artifact.content), historical,
                    expose_experimental_sku=True))
        errors, retry_failures = [], {}
        deadlines = [(row['data'].get('individual_rating') or {}).get('not_before', 0) for row in saved_cards]
        not_before = max((value for value in deadlines if type(value) in (int, float) and isfinite(value)), default=0)

        def rate(card):
            card['metrics'] = profile_metrics(card)
            if not_before > time():
                model_retry._wait_for_retry(not_before - time(), heartbeat)
            frozen_profile, frozen_stock = inputs[card.get('analysis_settings_snapshot_id') or str(run['settings_snapshot_id'])]
            snapshot = build_card_rating_input(card, frozen_profile, frozen_stock)

            def save(value):
                previous = card.get('individual_rating') or {}
                history = list(card.get('individual_rating_history') or [])
                if (previous.get('status') == 'complete' and previous.get('fingerprint') != value['fingerprint']
                        and not any(row.get('fingerprint') == previous['fingerprint'] for row in history)):
                    history.append(deepcopy(previous))
                updates = {'individual_rating': value, 'rating_version': INDIVIDUAL_RATING_VERSION}
                if history:
                    updates['individual_rating_history'] = history
                if value['status'] == 'complete':
                    updates['individual_rating_completed_refresh_id'] = card.get('analysis_completed_refresh_id')
                repository.upsert_card(run_id, card['candidate_id'], updates)
                card.update(updates)

            return rate_card(snapshot, self.model, card.get('analysis_completed_refresh_id') or str(job['job_id']),
                heartbeat=heartbeat, save=save, checkpoint=card.get('individual_rating'), retry_cycle=str(job['job_id']))

        def finish(future, card):
            nonlocal not_before
            try:
                result = future.result()
            except Exception as error:
                errors.append(error)
                return
            key = card['candidate_id']
            if result['status'] == 'complete':
                retry_failures.pop(key, None)
            else:
                details = result.get('error') or {'code': 'CARD_RATING_NOT_COMPLETED', 'message': 'Оценка не завершена.'}
                error = CoreError(details['code'], details['message'])
                if result['status'] == 'failed' and error.code in model_retry.RETRYABLE_MODEL_ERRORS:
                    retry_failures[key] = card, error
                    not_before = max(not_before, result.get('not_before', 0))
                else:
                    errors.append(error)

        def dispatch(selected):
            with ThreadPoolExecutor(max_workers=10, thread_name_prefix='saved-card-rating') as workers:
                pending = {}
                try:
                    for card in selected:
                        heartbeat()
                        while len(pending) >= 10:
                            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
                            for future in ready:
                                finish(future, pending.pop(future))
                        heartbeat()
                        if errors:
                            raise errors[0]
                        pending[workers.submit(rate, card)] = card
                finally:
                    for future, card in pending.items():
                        finish(future, card)

        try:
            dispatch(cards)
            for delay in model_retry.RETRY_DELAYS:
                selected = [card for card, _ in retry_failures.values()
                            if card['individual_rating'].get('attempts', 0) < model_retry.MAX_ATTEMPTS]
                if not selected or errors:
                    break
                model_retry._wait_for_retry(max(delay, not_before - time()), heartbeat)
                dispatch(selected)
            errors.extend(error for _, error in retry_failures.values())
            if errors:
                raise errors[0]
            return {'status': 'complete'}
        finally:
            self.repository.release_rating_owner(job['data_scope'], run_id, job['job_id'])

    def execute(self, job):
        operation = job['operation']
        heartbeat = lambda: self.jobs.heartbeat(job['job_id'])
        heartbeat()
        actor = ActorContext(job['actor_id'], job['data_scope'], frozenset({'artifact:read', 'process:read'}))
        snapshot = self.settings.read_snapshot(actor.data_scope, UUID(operation['settings_snapshot_id']))
        profile = snapshot['profile']
        artifact = self.artifacts.read(actor, ArtifactRef(UUID(profile['stock_artifact_id'])))
        if artifact.metadata['sha256'] != profile['stock_sha256']:
            raise CoreError('STALE_REVISION', 'Склад изменился после сохранения настроек.')
        run_id = UUID(operation.get('run_id') or str(job['job_id']))
        run = self.repository.read(actor.data_scope, run_id)
        if run is None:
            raise CoreError('NOT_FOUND', 'Снимок прогона не найден.')
        production_version = PRODUCTION_RULE_VERSION if operation['kind'] == 'profile_refresh' else run['data'].get('stage_versions', {}).get('production', 2)
        stock = stock_for_profile(self.parse_stock(artifact.content), profile, legacy_links=production_version == 1,
                                  expose_experimental_sku=production_version >= BASELINE_PRODUCTION_RULE_VERSION)
        if operation['kind'] == 'profile_metrics':
            execution = self.profiles.repository.execution(self.repository, actor.data_scope, run_id,
                job['job_id'], owner_field='metrics_job_id')
            pipeline = copy(self)
            pipeline.repository = execution
            state = deepcopy(run['data'])
            checkpoint = state.setdefault('metrics_checkpoint', {})
            def save_metrics():
                execution.update(actor.data_scope, run_id, state)
            try:
                refresh_metrics_cards(pipeline, job, run, profile, stock, checkpoint, save_metrics)
            finally:
                self.repository.release_metrics_owner(actor.data_scope, run_id, job['job_id'])
            return
        if operation['kind'] == 'profile_refresh':
            from bervel.modules.ai_tender_radar.profile_refresh import execute_profile_refresh
            execution = self.profiles.repository.execution(self.repository, actor.data_scope, run_id, job['job_id'])
            pipeline = copy(self)
            pipeline.repository = execution
            pipeline.profiles = copy(self.profiles)
            pipeline.profiles.repository = execution
            # The scenario persists completion/errors through its fenced execution
            # repository. A second raw finish here could bypass an expired lease.
            try:
                execute_profile_refresh(pipeline, job, run, profile, stock)
            except CoreError:
                raise
            except Exception as error:
                raise CoreError('PROFILE_REFRESH_FAILED', 'Не удалось завершить обновление профиля.') from error
            return
        if operation['kind'] == 'rating':
            try:
                self.rate_run(job, run, profile, stock)
            finally:
                if (operation.get('reference') or {}).get('rating_version') == INDIVIDUAL_RATING_VERSION:
                    self.repository.release_rating_owner(job['data_scope'], run_id, job['job_id'])
            return
        if operation['kind'] in {'question', 'review'}:
            from bervel.modules.ai_tender_radar.review import question_card, review_card
            projected = self.read(actor, run_id) if operation['kind'] == 'question' else None
            available_cards = projected['cards'] if projected else [row['data'] for row in self.repository.cards(run_id)]
            card = next((row for row in available_cards if row['analysis_id'] == operation['analysis_id']), None)
            if card is None:
                raise CoreError('NOT_FOUND', 'Карточка не найдена.')
            card_snapshot_id = card.get('analysis_settings_snapshot_id')
            if run['data'].get('profile_source') and card_snapshot_id and str(snapshot['id']) != card_snapshot_id:
                card_snapshot = self.settings.read_snapshot(actor.data_scope, UUID(card_snapshot_id))
                profile = card_snapshot['profile']
                artifact = self.artifacts.read(actor, ArtifactRef(UUID(profile['stock_artifact_id'])))
                if artifact.metadata['sha256'] != profile['stock_sha256']:
                    raise CoreError('STALE_REVISION', 'Склад отличается от сохранённого расчёта карточки.')
                stock = stock_for_profile(self.parse_stock(artifact.content), profile,
                    expose_experimental_sku=production_version >= BASELINE_PRODUCTION_RULE_VERSION)
            if operation['kind'] == 'question' and card.get('state') == 'analyzing':
                raise CoreError('CONFLICT', 'Дождитесь завершения анализа этой карточки.')
            if operation['kind'] == 'review':
                card = present_card(card, profile, stock)
                result = review_card(card, self.model, str(job['job_id']), heartbeat,
                                     prompt_version=ANALYTICAL_PROMPT_VERSION, stock=stock, profile=profile)
                self.repository.report(job['job_id'], run_id, {'kind': 'review', 'analysis_id': card['analysis_id'],
                    'projection_version': card['projection']['version'], 'result': result})
                return
            saved_reports = self.repository.read_reports(run_id)
            rating = matching_report(build_snapshot(available_cards), saved_reports)
            if rating is not None:
                card = dict(card, rating_context={key: rating['data']['result'].get(key)
                    for key in ('prompt_version', 'fingerprint', 'cohort_size', 'model', 'started_at')})
            history = [dict(question=entry['data']['question'], answer=entry['data']['result'].get('answer', ''),
                            calculations=entry['data']['result'].get('calculations', []))
                       for entry in saved_reports
                       if entry['data'].get('kind') == 'question'
                       and entry['data'].get('analysis_id') == card['analysis_id']
                       and (entry['data'].get('result') or {}).get('state') == 'complete']
            source = self.sources.get(run['data'].get('source', 'test_api'))
            fetch_options = {'allow_cancelled': True} if 'cancelled' in (run['data'].get('purchase_stages') or []) else {}
            fetch_source = (lambda: source.fetch_tender(card['candidate_id'], search_item=card.get('brief') or {},
                                                       **fetch_options)) if source and not run['data'].get('profile_source') else None
            result = question_card(card, operation['question'], self.model, str(job['job_id']), heartbeat,
                                   stock=stock, profile=profile, history=history, fetch_source=fetch_source)
            self.repository.report(job['job_id'], run_id, {'kind': 'question', 'analysis_id': card['analysis_id'],
                'question': operation['question'], 'result': result})
            return
        if operation['kind'] == 'audit':
            from bervel.modules.ai_tender_radar.audit import audit_run
            cards = [entry['data'] for entry in self.repository.cards(run_id)]
            loaded_ids = {card['candidate_id'] for card in cards}
            for candidate in (run or {}).get('data', {}).get('checkpoint', {}).get('candidates', []):
                if candidate['id'] not in loaded_ids:
                    cards.append({'analysis_id': 'brief:' + candidate['id'], 'candidate_id': candidate['id'],
                                  'brief': candidate['brief'], 'state': 'not_loaded'})
            result = audit_run(cards, stock, profile, self.model, str(job['job_id']), heartbeat,
                               operation.get('reference'), max_cards=operation.get('max_cards'),
                               progress=lambda result:self.repository.report(job['job_id'],run_id,{'kind':'audit','result':result}))
            self.repository.report(job['job_id'], run_id, {'kind': 'audit', 'result': result})
            return
        if run['data'].get('selection_version')==3:
            from bervel.modules.ai_tender_radar.named_runs import execute_named
            execute_named(self,job,run,profile,stock)
            finished = self.repository.read(actor.data_scope, run_id)
            if finished['data']['state'] == 'complete':
                state = deepcopy(finished['data'])
                counted = _BudgetedModel(self.model, state, float('inf'),
                    lambda: self.repository.update(actor.data_scope, run_id, state))
                self.rate_run(job, finished, profile, stock, model=counted)
            return
        state = deepcopy(run['data'])
        state.update(state='running', current_job_id=str(job['job_id']))
        state.setdefault('stage_versions', _stage_versions('selection-v1', legacy=True))
        budgets = state['budgets']
        def save():
            heartbeat()
            self.repository.update(actor.data_scope, run_id, state)
        model = _BudgetedModel(self.model, state, budgets['model'], save)
        saved_source = state.get('source_kind') == 'saved_card'
        if not saved_source:
            require_simulator(self.source)
        if saved_source:
            old = next((row['data'] for row in self.repository.cards(UUID(state['source_run_id']))
                        if row['data']['analysis_id'] == state['source_analysis_id']), None)
            if old is None:
                raise CoreError('NOT_FOUND', 'Исходная запись пересчёта недоступна.')
            source = deepcopy(old.get('raw_tender') or old['tender'])
            self.repository.add_card(run_id, old['candidate_id'], {'analysis_id': str(uuid4()),
                'candidate_id': old['candidate_id'], 'brief': old['brief'], 'tender': source, 'raw_tender': source,
                'evaluated_at': datetime.now(timezone.utc).isoformat(), 'state': 'analyzing',
                'analysis': None, 'fulfillment': None, 'assessment': None, 'review': None,
                'source_analysis_id': old['analysis_id']})
        if not saved_source and 'limits' not in state:
            limits = self.source.read_limits()
            state['limits'] = limits
            remaining = {g.get('LimitGroup'): g['AvailableRequests'] for g in limits}
            if limits and not {'search', 'purchase'} <= remaining.keys():
                raise CoreError('KONTUR_INVALID_RESPONSE', 'Источник не сообщил отдельные квоты поиска и карточек.')
            state['effective_budgets'] = {'search': min(budgets['search'], remaining.get('search', budgets['search'])),
                                          'detail': min(budgets['detail'], remaining.get('purchase', budgets['detail']))}
            save()
        state['phase'] = 'search' if not saved_source else 'details'
        def checkpoint(value):
            state['checkpoint'] = value
            save()
        collected = state.get('checkpoint', {}) if saved_source else collect_candidates(self.source, search_queries(profile),
            search_max=state['effective_budgets']['search'], checkpoint=state.get('checkpoint', {}), progress=checkpoint,
            archive_page=lambda page: self.repository.archive_page(run_id, page))
        state['checkpoint'] = collected
        if 'selection' not in state:
            state['phase'] = 'ranking'
            save()
            ranked = rank_candidates(collected['candidates'], profile)
            selected, warnings = rerank_candidates(ranked, profile, model, str(run_id),
                detail_max=state['effective_budgets']['detail'], heartbeat=heartbeat)
            state['selection'] = selected
            state['ranking_warnings'] = warnings
            state['ranked_candidates'] = ranked
            save()
        state['phase'] = 'details'
        saved_cards = {row['candidate_id']: row['data'] for row in self.repository.cards(run_id)}
        done = {key for key, card in saved_cards.items() if card.get('state') != 'analyzing'}
        for candidate in state['selection']:
            if candidate['id'] in done:
                continue
            card = deepcopy(saved_cards.get(candidate['id']))
            if card is None and state.get('detail_attempts', 0) >= state['effective_budgets']['detail']:
                break
            if card is None:
                state['detail_attempts'] = state.get('detail_attempts', 0) + 1
                state['pending_detail'] = candidate['id']
                save()
                card = {'analysis_id': str(uuid4()), 'candidate_id': candidate['id'], 'brief': candidate['brief'],
                    'evaluated_at': datetime.now(timezone.utc).isoformat(), 'tender': None, 'analysis': None,
                    'fulfillment': None, 'assessment': None, 'review': None}
            try:
                tender = card['tender']
                if tender is None:
                    tender = self.source.fetch_tender(candidate['id'], search_item=candidate['brief'])
                    card.update(tender=tender, state='analyzing')
                    card['raw_tender'] = deepcopy(tender)
                    heartbeat()
                    self.repository.add_card(run_id, candidate['id'], card)
                if not card.get('input_finalized'):
                    if tender.get('documents'):
                        tender = enrich_tender(tender, model, f'documents-{card["analysis_id"]}', heartbeat,
                            profile=profile, prompt_version=state['stage_versions'].get('documents', LEGACY_DOCUMENT_PROMPT_VERSION))
                    card.update(tender=tender, input_finalized=True)
                    heartbeat()
                    self.repository.prepare_card(run_id, candidate['id'], tender)
                completeness = check_completeness(tender)
                card.update(tender=tender, completeness=completeness)
                matching = match_positions(tender, stock, model, f'run-{run_id}-{candidate["id"]}', heartbeat)
                analysis = calculate_analysis(tender, stock, matching)
                analysis['mock'] = True
                plan = evaluate_fulfillment(tender, stock, analysis, profile,
                                            now=datetime.fromisoformat(card['evaluated_at']), rule_version=production_version)
                assessment = score_assessment(tender, plan, profile.get('scoring_policy'))
                relevant = any(p.get('matches') for p in analysis['positions'])
                uncertain = bool(analysis.get('model_error')) or any(p.get('stock_status') == 'unconfirmed' for p in analysis['positions'])
                card.update(analysis=analysis, fulfillment=plan, assessment=assessment,
                    state='needs_data' if uncertain or not completeness['complete'] else 'accepted' if relevant else 'irrelevant')
                from bervel.modules.ai_tender_radar.review import review_card
                card['review'] = review_card(card, model, f'review-{card["analysis_id"]}', heartbeat)
            except CoreError as error:
                if error.code in {'KONTUR_RATE_LIMIT', 'KONTUR_AUTH_ERROR', 'KONTUR_ACCESS_DENIED', 'KONTUR_UNAVAILABLE'}:
                    state['stop_reason'] = error.code
                    save()
                    raise
                card.update(state='failed', error={'code': error.code, 'message': error.message})
            heartbeat()
            if card.get('tender') is None:
                self.repository.add_card(run_id, candidate['id'], card)
            else:
                self.repository.finalize_card(run_id, candidate['id'], card)
            state['pending_detail'] = None
            done.add(candidate['id'])
            state['processed'] = len(done)
            save()
        cards = self.repository.cards(run_id)
        state.update(state='complete', phase='complete', stop_reason='saved_card' if saved_source else
            'detail_budget' if state.get('detail_attempts', 0) >= state['effective_budgets']['detail'] else collected['stop_reason'],
            counts={kind: sum(row['data'].get('state') == kind for row in cards)
                    for kind in ('accepted', 'needs_data', 'irrelevant', 'failed')})
        state['counts']['looked'] = len(collected.get('candidates', []))
        save()
        self.rate_run(job, dict(run, data=state), profile, stock, model=model)
