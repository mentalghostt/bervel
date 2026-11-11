"""One incremental refresh scenario for the two persistent source profiles."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from math import isfinite
from threading import RLock
from time import time
from uuid import UUID

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar import brief_ranking as model_retry
from bervel.modules.ai_tender_radar.brief_ranking import (
    AI_SELECTION_VERSION, CARD_LOCAL_ERRORS, MAX_ATTEMPTS, RETRYABLE_MODEL_ERRORS, RETRY_DELAYS,
    rank_briefs_with_model,
)
from bervel.modules.ai_tender_radar.individual_rating import (
    PROMPT_VERSION as INDIVIDUAL_RATING_VERSION, build_card_rating_input, rate_card, saved_rating,
)
from bervel.modules.ai_tender_radar.named_runs import _CountedModel, _analyze
from bervel.modules.ai_tender_radar.profile_policy import card_actuality, profile_metrics, search_fingerprint
from bervel.modules.ai_tender_radar.selection import model_search_queries, require_simulator
from bervel.modules.company_settings import active_products
from bervel.modules.ai_tender_radar.metrics_refresh import (
    REANALYSIS_REFRESH_VERSION, INCREMENTAL_REFRESH_VERSIONS,
    refresh_metrics_cards, prepare_metrics_inputs, metrics_dependencies,
    selected_skus, full_input_fingerprint, fingerprint as metrics_fingerprint,
    project_metrics,
)


MAX_FULL_CARD_LIFECYCLES = 10


def _latest_metadata(card: dict) -> dict:
    """Update normalized metadata, retaining the original details and documents."""
    tender = deepcopy(card.get('tender'))
    if not tender:
        return card
    brief = card.get('brief') or {}
    source = tender.setdefault('source_card', {})
    for key in ('ApplicationDeadline', 'TypeOfTrading', 'Law', 'IsPlanning', 'IsCancelled', 'IsAbandoned',
                'PurchaseStatus', 'PurchaseStage', 'Stage', 'Status', 'NotificationNumber', 'Link'):
        if key in brief:
            source[key] = deepcopy(brief[key])
    for original, normalized in (('OrderName', 'title'), ('NotificationNumber', 'number'), ('Link', 'link'),
                                 ('ApplicationDeadline', 'application_deadline')):
        if original in brief:
            tender[normalized] = brief[original]
    if 'OrderName' in brief:
        source['Title'] = brief['OrderName']
    if 'MaxPrice' in brief:
        source['InitialSum'] = {'Price': brief['MaxPrice'], 'Currency': brief.get('Currency')}
        tender['initial_sum'] = deepcopy(source['InitialSum'])
    elif 'Currency' in brief and isinstance(source.get('InitialSum'), dict):
        source['InitialSum']['Currency'] = brief['Currency']
    tender['source_stage_flags'] = dict(tender.get('source_stage_flags') or {}, **{
        key: brief[key] for key in ('IsPlanning', 'IsCancelled', 'IsAbandoned') if key in brief})
    return dict(card, tender=tender)


def _budgets(source, limits: list[dict], detail_max: int | None) -> dict:
    if not limits and getattr(source, 'simulated', False):
        search, detail = 200, 200
    else:
        groups = {row.get('LimitGroup'): row.get('AvailableRequests') for row in limits if isinstance(row, dict)}
        search, detail = groups.get('search'), groups.get('purchase')
        if any(type(value) is not int or value < 0 for value in (search, detail)):
            raise CoreError('KONTUR_INVALID_RESPONSE', 'Источник не сообщил отдельные квоты поиска и карточек.')
    return {'search': min(200, search), 'detail': detail if detail_max is None else min(detail, detail_max)}


def execute_profile_refresh(pipeline, job: dict, run: dict, profile: dict, stock: dict) -> None:
    """Persist briefs before AI; fetch only previously unrequested details.

    Every source attempt has a durable reservation before I/O. A restarted job
    never repeats a request whose outcome is unknown. A new refresh can search
    anew, while unknown detail reservations remain attached to their cards.
    """
    operation = job['operation']
    run_id, job_id = UUID(str(operation['run_id'])), str(job['job_id'])
    refresh_id = operation.get('refresh_id') or job_id
    scope, source_kind = job['data_scope'], operation['profile_source']
    repository, profiles, lock = pipeline.repository, pipeline.profiles.repository, RLock()
    state = deepcopy(run['data'])
    checkpoint = state.get('refresh_checkpoint') or {}
    from bervel.modules.ai_tender_radar.runs import _stage_versions
    if checkpoint.get('job_id') == refresh_id and checkpoint.get('finished') and state.get('state') == 'complete':
        return
    if checkpoint.get('job_id') != refresh_id:
        checkpoint = {'job_id': refresh_id, 'page': 0, 'seen_ids': [], 'search_attempts': 0,
            'detail_attempts': 0, 'analyzed_ids': [], 'search_start': '1970-01-01T00:00:00Z',
            'search_end': datetime.now(timezone.utc).isoformat()}
        state.update(model_attempts=0, pending_detail=None, ranked_candidates=[])
        state['stage_versions'] = dict(_stage_versions(AI_SELECTION_VERSION), rating=INDIVIDUAL_RATING_VERSION)
        if operation.get('refresh_version') in INCREMENTAL_REFRESH_VERSIONS:
            state['stage_versions'].update(refresh=operation['refresh_version'], documents='document-extraction-v4')
    state.update(state='running', phase='search', current_job_id=job_id,
                 profile_source=source_kind, source=source_kind, refresh_checkpoint=checkpoint)
    state.pop('error', None)
    state.setdefault('stage_versions', _stage_versions(AI_SELECTION_VERSION))
    individual_rating = state['stage_versions'].get('rating') == INDIVIDUAL_RATING_VERSION
    incremental = state['stage_versions'].get('refresh') in INCREMENTAL_REFRESH_VERSIONS
    scoped_reanalysis = state['stage_versions'].get('refresh') == REANALYSIS_REFRESH_VERSION
    state.pop('completion_summary', None)
    stage_errors = checkpoint.setdefault('completion_errors', {}) if incremental else {}
    if incremental:
        for stage in ('primary', 'detail', 'analysis', 'rating', 'metrics'):
            stage_errors.setdefault(stage, {})
    prepared_metrics = prepare_metrics_inputs(profile, stock) if incremental else None
    if incremental:
        pipeline.metrics_prepared = prepared_metrics
    saved = {row['candidate_id']: deepcopy(row['data']) for row in repository.cards(run_id)}

    def heartbeat():
        with lock:
            pipeline.jobs.heartbeat(job['job_id'])

    def save(*, check_stop=True):
        with lock:
            if check_stop:
                heartbeat()
            state['detail_attempts'] = checkpoint['detail_attempts']
            state['search_attempts'] = checkpoint['search_attempts']
            if 'limits' in checkpoint:
                groups = {row['LimitGroup']: row['AvailableRequests'] for row in checkpoint['limits']}
                if {'search', 'purchase'} <= groups.keys():
                    state['quotas'] = {'search': max(0, groups['search'] - checkpoint['search_attempts']),
                        'detail': max(0, groups['purchase'] - checkpoint['detail_attempts'])}
                else:
                    state.pop('quotas', None)
            state['checkpoint'] = checkpoint
            if state.get('phase') not in {'failed', 'complete'}:
                checkpoint['phase'] = state['phase']
            repository.update(scope, run_id, state)

    def put(card):
        with lock:
            card = _latest_metadata(card)
            card['metrics'] = profile_metrics(card)
            card['inactive_reason'] = card['metrics'].get('inactivity_reason')
            value = profiles.upsert_card(run_id, card['candidate_id'], card)
            saved[card['candidate_id']] = value
            return value

    def progress():
        state['counts'] = {kind: sum(card.get('state') == kind for card in saved.values())
                           for kind in ('primary', 'accepted', 'needs_data', 'irrelevant', 'failed')}
        state['counts']['looked'] = len(saved)
        state['processed'] = len(checkpoint['analyzed_ids'])
        state['unverified_count'] = sum(not card.get('details_loaded') for card in saved.values())
        save()

    try:
        heartbeat()
        if incremental:
            state['phase'] = 'metrics'
            save()
            try:
                refresh_metrics_cards(pipeline, job, dict(run, data=state), profile, stock, checkpoint,
                                      lambda: save(check_stop=False))
            except CoreError as error:
                if error.code != 'METRICS_PARTIAL_FAILURE':
                    raise
            stage_errors['metrics'] = deepcopy(checkpoint.get('metrics_errors', {}))
            saved = {row['candidate_id']: deepcopy(row['data']) for row in repository.cards(run_id)}
            state['phase'] = 'search'
            save()
        recovered_page = None
        source = pipeline.sources.get(source_kind)
        if source_kind not in {'test_api', 'kontur_api'} or source is None or not getattr(source, 'key', None):
            raise CoreError('KONTUR_SOURCE_NOT_CONFIGURED', 'Выбранный источник закупок не настроен.')
        if source_kind == 'test_api':
            require_simulator(source)
        if checkpoint.get('pending_search'):
            pending_search = checkpoint['pending_search']
            archived = repository.source_pages(run_id) if hasattr(repository, 'source_pages') else []
            recovered_page = next((entry['data'] for entry in reversed(archived)
                if (entry['data'].get('refresh_id') == refresh_id
                    or entry['data'].get('job_id') in {job_id, refresh_id, operation.get('resume_of'), pending_search.get('request_job_id')})
                and entry['data'].get('page') == pending_search['page']
                and entry['data'].get('search_keywords') == pending_search['keywords']
                and entry['data'].get('start') == checkpoint['search_start']
                and entry['data'].get('end') == checkpoint['search_end']), None)
            if recovered_page is None:
                raise CoreError('SOURCE_REQUEST_OUTCOME_UNKNOWN',
                    'Исход предыдущего поискового запроса неизвестен. Автоматический повтор не отправлен.')
        if state.get('pending_detail'):
            key = state['pending_detail']
            if key in saved and not saved[key].get('details_loaded'):
                put(dict(saved[key], detail_request_state='unknown'))
            state['pending_detail'] = None
        # A process can die after the card reservation but before the state write.
        for card in list(saved.values()):
            if card.get('detail_request_state') == 'pending' and not card.get('details_loaded'):
                put(dict(card, detail_request_state='unknown'))
        def model_cooldown():
            # A provider cooldown gates every later call, including peer analyses.
            while checkpoint.get('card_rating_not_before', 0) > time():
                model_retry._wait_for_retry(checkpoint['card_rating_not_before'] - time(), heartbeat)

        class CooldownModel:
            def generate(self, *args, **kwargs):
                try:
                    return pipeline.model.generate(*args, **kwargs)
                except CoreError as error:
                    delay = getattr(error, 'retry_after_seconds', None)
                    if type(delay) in (int, float) and isfinite(delay) and delay > 0:
                        with lock:
                            checkpoint['card_rating_not_before'] = max(
                                checkpoint.get('card_rating_not_before', 0), time() + delay)
                            try:
                                save(check_stop=False)
                            except CoreError:
                                raise
                            except Exception as failure:
                                raise CoreError('PERSISTENCE_ERROR', 'Не удалось сохранить ожидание провайдера.') from failure
                    raise

        model = _CountedModel(CooldownModel() if incremental else pipeline.model, state, lock, save,
                              before_generate=model_cooldown if individual_rating else None)
        if 'effective_budgets' not in checkpoint:
            limits = source.read_limits()
            checkpoint['limits'] = limits
            checkpoint['effective_budgets'] = _budgets(source, limits, operation['detail_max'])
            state.update(limits=limits, effective_budgets=checkpoint['effective_budgets'])
            save()
        if 'search_keywords' not in checkpoint:
            keywords = list(operation['keywords'])
            if operation['keyword_mode'] == 'auto':
                products = sorted(active_products(profile), key=lambda product: product['id'])
                product_fingerprint = hashlib.sha256(json.dumps(products, sort_keys=True, ensure_ascii=False,
                    separators=(',', ':')).encode()).hexdigest()
                cache = state.get('auto_keyword_cache') or {}
                if cache.get('products_fingerprint') == product_fingerprint:
                    keywords = list(cache['keywords'])
                else:
                    keywords = model_search_queries(profile, model, job_id, heartbeat=heartbeat)
                    state['auto_keyword_cache'] = {'products_fingerprint': product_fingerprint, 'keywords': keywords}
            if not keywords:
                raise CoreError('VALIDATION_ERROR', 'Для поиска нужны непустые ключевые фразы.')
            checkpoint.update(search_keywords=keywords, search_fingerprint=search_fingerprint(keywords))
            state['search_fingerprint'] = checkpoint['search_fingerprint']
            save()
        fingerprint = checkpoint['search_fingerprint']
        seen = set(checkpoint['seen_ids'])
        budget = checkpoint['effective_budgets']
        while not checkpoint.get('search_done') and (recovered_page is not None or checkpoint['search_attempts'] < budget['search']) and checkpoint['page'] < 200:
            heartbeat()
            page = checkpoint['page']
            if recovered_page is not None:
                response = recovered_page['response']
                recovered_page = None
            else:
                checkpoint['search_attempts'] += 1
                checkpoint['pending_search'] = {'page': page, 'keywords': checkpoint['search_keywords'],
                                                'request_job_id': job_id}
                save()
                response = source.search_page(checkpoint['search_keywords'], page, checkpoint['search_start'],
                    checkpoint['search_end'], purchase_statuses=[1, 4])
                # Archive the received input even when subsequent validation fails.
                page_id = repository.archive_page(run_id, {'phrase': 'Общий запрос', 'page': page,
                    'start': checkpoint['search_start'], 'end': checkpoint['search_end'], 'response': response,
                    'search_keywords': checkpoint['search_keywords'], 'purchase_statuses': [1, 4],
                    'job_id': job_id, 'refresh_id': refresh_id})
                checkpoint.setdefault('page_refs', []).append(page_id)
            if not isinstance(response, dict):
                raise CoreError('KONTUR_INVALID_RESPONSE', 'Поисковый ответ должен быть объектом.')
            items, total = response.get('Items'), response.get('TotalCount')
            if items is None and total == 0:
                items = []
            if (type(total) is not int or total < 0 or not isinstance(items, list)
                    or response.get('PageNumber') != page
                    or len(items) != min(50, max(0, total - page * 50))):
                raise CoreError('KONTUR_INVALID_RESPONSE', 'Не удалось проверить полноту поисковой страницы.')
            if 'total' in checkpoint and checkpoint['total'] != total:
                checkpoint['unstable_results'] = True
            checkpoint['total'] = total
            now = datetime.now(timezone.utc).isoformat()
            for brief in items:
                key = brief.get('Id') if isinstance(brief, dict) else None
                if not isinstance(key, str) or not re.fullmatch(r'[\w.\-]{1,128}', key):
                    raise CoreError('KONTUR_INVALID_RESPONSE', 'В поиске отсутствует корректный ID.')
                if key in seen:
                    checkpoint['duplicate_ids'] = True
                else:
                    seen.add(key)
                    checkpoint['seen_ids'].append(key)
                previous = saved.get(key, {'raw_tender': None, 'tender': None, 'analysis': None,
                    'fulfillment': None, 'assessment': None, 'review': None,
                    'priority_score': None, 'priority_reason': None})
                loaded = bool(previous.get('details_loaded') or previous.get('raw_tender'))
                # A new authoritative open/planned search result supersedes a
                # previous definite 404/closed response, never an unknown attempt.
                restored = {'source_unavailable': False, 'source_closed': False}
                if previous.get('detail_request_state') == 'unavailable':
                    restored['detail_request_state'] = None
                put(dict(previous, candidate_id=key, brief=deepcopy(brief),
                    information_state='full' if loaded else 'primary', details_loaded=loaded,
                    state=previous.get('state', 'primary'), last_seen_at=now,
                    search_fingerprint=fingerprint, source_missing=False, **restored))
            checkpoint.update(page=page + 1, pending_search=None,
                search_done=(page + 1) * 50 >= total)
            save()
        exhaustive = bool(checkpoint.get('search_done') and checkpoint.get('total', 10000) < 10000
            and len(seen) == checkpoint['total'] and not checkpoint.get('unstable_results')
            and not checkpoint.get('duplicate_ids'))
        checkpoint['stop_reason'] = ('search_exhausted' if exhaustive else
            'search_cap' if checkpoint['page'] >= 200 else 'search_incomplete')
        state['scope_complete'] = exhaustive
        # Checking stop here is essential: a stop after the final HTTP response
        # must not turn an unfinished refresh into a mass disappearance event.
        heartbeat()
        for key, card in list(saved.items()):
            if exhaustive and key not in seen and card.get('search_fingerprint') == fingerprint:
                put(dict(card, source_missing=True))
            else:
                put(card)  # Time-driven expiry also applies outside the current keyword scope.
        state['phase'] = 'ranking'
        progress()
        if not checkpoint.get('ranked'):
            eligible_ids = [key for key, card in saved.items() if not card.get('details_loaded')
                and card.get('detail_request_state') not in {'unknown', 'pending', 'unavailable'}
                and card_actuality(card)['actuality'] == 'active']
            checkpoint.setdefault('ranking_candidate_ids', eligible_ids)
            candidates = [{'id': key, 'brief': saved[key]['brief'], 'state': 'candidate'}
                          for key in checkpoint['ranking_candidate_ids']]
            cached = {}
            for row in candidates:
                prior = saved[row['id']]
                evidence = prior.get('priority_evidence')
                score = evidence.get('score') if isinstance(evidence, dict) else None
                valid = type(score) is int and 0 <= score <= 100 and evidence.get('id', row['id']) == row['id']
                if valid and (incremental or prior.get('priority_refresh_id') == refresh_id):
                    cached[row['id']] = evidence
            retry_cycle = operation.get('priority_retry_cycle') or refresh_id
            previous_retry = checkpoint.get('ranking_retry') or {}
            if previous_retry.get('cycle_id') != retry_cycle:
                checkpoint['ranking_retry'] = {'cycle_id': retry_cycle, 'attempts': {},
                    'not_before': previous_retry.get('not_before', 0)}
            retry = checkpoint['ranking_retry']
            # Ranking owns a local error map while peers reserve model calls.
            # Publish snapshots only through the same lock as run persistence.
            primary_errors = deepcopy(stage_errors['primary']) if incremental else None

            def attempt_progress(key, count):
                with lock:
                    retry['attempts'][key] = count

            def cooldown_progress(deadline):
                with lock:
                    retry['not_before'] = deadline

            def ranking_progress(evaluations, pending_ids, completed):
                with lock:
                    if incremental:
                        stage_errors['primary'] = deepcopy(primary_errors)
                    for key, evidence in evaluations.items():
                        if key not in cached and saved[key].get('priority_refresh_id') != refresh_id:
                            put(dict(saved[key], priority_score=evidence['score'], priority_reason=evidence.get('reason'),
                                priority_evidence=evidence, priority_refresh_id=refresh_id,
                                **({'priority_prompt_version': 'brief-priority-scalar-v1',
                                    'priority_input_fingerprint': metrics_fingerprint([saved[key]['brief'],
                                        prepared_metrics['technical']])} if incremental else {})))
                    checkpoint.update(ranking_completed=completed, ranking_total=len(candidates),
                                      ranking_pending_ids=list(pending_ids))
                    # Dispatch checks Stop separately. Preserve completed calls
                    # while draining the ten in-flight requests after a Stop.
                    save(check_stop=False)
            ranked = rank_briefs_with_model(candidates, profile, model, refresh_id, heartbeat=heartbeat,
                                           saved_evaluations=cached, progress=ranking_progress,
                                           attempts=retry['attempts'], attempt_progress=attempt_progress,
                                           not_before=retry.get('not_before', 0), cooldown_progress=cooldown_progress,
                                           local_errors=primary_errors)
            if incremental:
                stage_errors['primary'] = deepcopy(primary_errors)
            checkpoint['ranked_ids'] = [candidate['id'] for candidate in ranked]
            checkpoint['ranked'] = True
            state['ranked_candidates'] = ranked
            save()
        state['phase'] = 'details'
        progress()
        for key in checkpoint.get('ranked_ids', []):
            card = saved[key]
            if (card.get('details_loaded') or card.get('detail_request_state') in {'unknown', 'pending', 'unavailable'}
                    or card_actuality(card)['actuality'] != 'active'):
                continue
            if checkpoint['detail_attempts'] >= budget['detail']:
                break
            heartbeat()
            put(dict(card, detail_request_state='pending'))
            if incremental:
                checkpoint.setdefault('detail_candidate_ids', []).append(key)
            checkpoint['detail_attempts'] += 1
            state['pending_detail'] = key
            save()
            try:
                value = source.fetch_tender(key, search_item=card['brief'])
            except CoreError as error:
                unavailable = error.code in {'KONTUR_NOT_FOUND', 'KONTUR_TENDER_CLOSED'}
                put(dict(saved[key], detail_request_state='unavailable' if unavailable else 'unknown',
                    inactive_reason='source_unavailable' if unavailable else None,
                    source_unavailable=error.code == 'KONTUR_NOT_FOUND',
                    source_closed=error.code == 'KONTUR_TENDER_CLOSED',
                    detail_error={'code': error.code, 'message': error.message}))
                if incremental:
                    stage_errors['detail'][key] = {'code': error.code, 'message': error.message}
                if not unavailable:
                    raise
            else:
                # Save received source details before heartbeat/model work. A stop
                # arriving during HTTP must not discard already paid input.
                put(dict(saved[key], raw_tender=deepcopy(value), tender=deepcopy(value),
                    details_loaded=True, information_state='full', detail_request_state='loaded',
                    details_loaded_at=datetime.now(timezone.utc).isoformat(), input_finalized=False,
                    **({'details_loaded_refresh_id': refresh_id} if scoped_reanalysis else {})))
                if incremental:
                    stage_errors['detail'].pop(key, None)
            state['pending_detail'] = None
            save()
        state['phase'] = 'analysis'
        progress()

        analysis_errors = []
        rating_failures = {}
        analysis_failures = {}
        analysis_cycle = operation.get('priority_retry_cycle') or refresh_id
        previous_analysis_retry = checkpoint.get('analysis_retry') or {}
        if previous_analysis_retry.get('cycle_id') != analysis_cycle:
            checkpoint['analysis_retry'] = {'cycle_id': analysis_cycle, 'attempts': {}}
        analysis_retry = checkpoint['analysis_retry']['attempts']

        def commit_analysis(result, original):
            # A failed model must not destroy an existing verified calculation.
            model_error = (result.get('analysis') or {}).get('model_error')
            error = result.get('error') or model_error or (result.get('review') or {}).get('error')
            if original.get('analysis') and (result.get('state') == 'failed' or model_error):
                result = dict(original, analysis_refresh_error=result.get('error') or model_error,
                              analysis_stale=True, **({'analysis_stage_cache': result['analysis_stage_cache']}
                                if result.get('analysis_stage_cache') else {}))
            else:
                result.update(analysis_stale=False, analysis_refresh_error=None)
                if result.get('analysis') and not model_error and result.get('state') != 'failed':
                    result['analysis_settings_snapshot_id'] = operation['settings_snapshot_id']
                    if not error:
                        result['analysis_completed_refresh_id'] = refresh_id
                        if incremental:
                            deps = metrics_dependencies(profile, stock, skus=selected_skus(result), prepared=prepared_metrics)
                            result['analysis_metrics_dependencies'] = deps
                            result['full_input_fingerprint'] = full_input_fingerprint(result, deps,
                                now=datetime.fromisoformat(result['evaluated_at']))
                            # A completed full analysis supersedes the numerical prelude.
                            result['metrics_projection'] = None
                            checkpoint.get('metrics_errors', {}).pop(result['candidate_id'], None)
                            if result.get('analysis_stage_cache'):
                                result['analysis_stage_cache'] = {key: value for key, value in result['analysis_stage_cache'].items()
                                    if key not in {'calculation', 'review_result'}}
            put(result)
            if incremental and not error:
                stage_errors['analysis'].pop(original['candidate_id'], None)
                analysis_failures.pop(original['candidate_id'], None)
            if not error and original['candidate_id'] not in checkpoint['analyzed_ids']:
                checkpoint['analyzed_ids'].append(original['candidate_id'])
            checkpoint['analysis_completed'] = len(checkpoint['analyzed_ids'])
            save(check_stop=False)
            return CoreError(error['code'], error['message']) if error else None

        def finish(future, original):
            try:
                result = future.result()
            except Exception as error:
                # A lifecycle may have committed its analysis before rating died.
                # Never restore the pre-analysis copy over that durable result.
                analysis_errors.append(error)
                return
            with lock:
                error = commit_analysis(result, original)
                if error:
                    analysis_errors.append(error)

        def rate_analyzed_card(key):
            with lock:
                original = deepcopy(saved[key])
            if original.get('analysis_completed_refresh_id') != refresh_id:
                result = _analyze(original, pipeline, run_id, stock, profile, None,
                                  model, lock, heartbeat, state['stage_versions'])
                with lock:
                    error = commit_analysis(result, original)
                if error:
                    raise error
            with lock:
                card = deepcopy(saved[key])
            if not card.get('analysis'):
                return
            snapshot = build_card_rating_input(card, profile, stock)

            def save_rating(value):
                with lock:
                    current = saved[key]
                    previous = current.get('individual_rating') or {}
                    history = list(current.get('individual_rating_history') or [])
                    if (previous.get('status') == 'complete' and previous.get('fingerprint') != value['fingerprint']
                            and not any(item.get('fingerprint') == previous['fingerprint'] for item in history)):
                        history.append(deepcopy(previous))
                    updates = {'individual_rating': value, 'rating_version': INDIVIDUAL_RATING_VERSION}
                    if history:
                        updates['individual_rating_history'] = history
                    if value['status'] == 'complete':
                        updates['individual_rating_completed_refresh_id'] = refresh_id
                    put(dict(current, **updates))
                    checkpoint['rating_completed'] = sum(
                        row.get('individual_rating_completed_refresh_id') == refresh_id
                        and saved_rating(row)['status'] == 'complete' for row in saved.values())
                    save(check_stop=False)

            result = rate_card(snapshot, model, refresh_id, heartbeat=heartbeat, save=save_rating,
                checkpoint=card.get('individual_rating'),
                retry_cycle=operation.get('priority_retry_cycle') or refresh_id)
            if result['status'] != 'complete':
                details = result.get('error') or {'code': 'CARD_RATING_NOT_COMPLETED',
                    'message': 'Оценка карточки не завершена.'}
                raise CoreError(details['code'], details['message'])

        # Source calls stay serial under the adapter gate; only analysis fans out.
        completed = set(checkpoint['analyzed_ids']) | {key for key, card in saved.items()
            if card.get('analysis_completed_refresh_id') == refresh_id}
        checkpoint['analyzed_ids'] = sorted(completed)
        full_cards = [card for card in saved.values() if card.get('details_loaded') and card.get('tender')
            and (not incremental or scoped_reanalysis or card_actuality(card)['actuality'] == 'active')
            and (not scoped_reanalysis or operation.get('reanalyze_existing', False)
                 or card.get('details_loaded_refresh_id') == refresh_id)]
        if scoped_reanalysis:
            selected_ids = {card['candidate_id'] for card in full_cards}
            completed &= selected_ids
            checkpoint['analyzed_ids'] = sorted(completed)
        if incremental:
            cohort_ids = {card['candidate_id'] for card in full_cards}
            for stage in ('analysis', 'rating'):
                stage_errors[stage] = {key: error for key, error in stage_errors[stage].items() if key in cohort_ids}
            for key in completed:
                stage_errors['analysis'].pop(key, None)
        reused = set()
        if incremental and not scoped_reanalysis:
            now = datetime.now(timezone.utc)
            for card in full_cards:
                projection = card.get('metrics_projection') or {}
                if not card.get('analysis') or (card['analysis'] or {}).get('model_error') or card.get('analysis_stale'):
                    continue
                deps = metrics_dependencies(profile, stock, skus=selected_skus(card), prepared=prepared_metrics)
                prior = card.get('full_input_fingerprint')
                if prior is None and projection.get('origin_dependencies'):
                    try:
                        prior = full_input_fingerprint(card, projection['origin_dependencies'],
                            now=datetime.fromisoformat(card['evaluated_at']))
                    except (KeyError, ValueError, TypeError):
                        pass
                if (prior == full_input_fingerprint(card, deps, now=now)
                        and project_metrics(card).get('rating', {}).get('status') != 'stale'
                        and (card.get('individual_rating') or {}).get('status') == 'complete'
                        and card.get('review') and not card['review'].get('error')
                        and card['review'].get('state') != 'failed'):
                    reused.add(card['candidate_id'])
            completed |= reused
            checkpoint['analyzed_ids'] = sorted(completed)
        candidates = [card for card in full_cards if card['candidate_id'] not in completed]
        checkpoint.update(analysis_completed=len(completed), analysis_total=len(completed) + len(candidates))
        save()
        if individual_rating:
            rated = {key for key, card in saved.items()
                if card.get('individual_rating_completed_refresh_id') == refresh_id
                and saved_rating(card)['status'] == 'complete'
                and (not scoped_reanalysis or key in selected_ids)}
            rated |= reused
            if incremental:
                for key in rated:
                    stage_errors['rating'].pop(key, None)
            checkpoint.update(rating_completed=len(rated), rating_total=len(full_cards))
            candidates = [card for card in full_cards if card['candidate_id'] not in completed
                          or card['candidate_id'] not in rated]
            limit = MAX_FULL_CARD_LIFECYCLES
        else:
            limit = 4
        def collect(future, original):
            if not individual_rating:
                finish(future, original)
                return
            key = original['candidate_id']
            error = None
            try:
                future.result()
            except Exception as failure:
                error = failure
            # A peer may be serializing this same checkpoint. Waiting on the
            # future stays outside the lock; every shared mutation stays inside.
            with lock:
                if error is None:
                    rating_failures.pop(key, None)
                    if incremental:
                        stage_errors['analysis'].pop(key, None)
                        stage_errors['rating'].pop(key, None)
                else:
                    rating = saved[key].get('individual_rating') or {}
                    local = incremental and isinstance(error, CoreError) and error.code in CARD_LOCAL_ERRORS
                    if local:
                        stage = ('analysis' if saved[key].get('analysis_completed_refresh_id') != refresh_id else 'rating')
                        stage_errors[stage][key] = {'code': error.code, 'message': error.message}
                    if (isinstance(error, CoreError) and error.code in RETRYABLE_MODEL_ERRORS
                            and incremental and saved[key].get('analysis_completed_refresh_id') != refresh_id):
                        analysis_failures[key] = error
                    elif (isinstance(error, CoreError) and error.code in RETRYABLE_MODEL_ERRORS
                            and rating.get('status') == 'failed'):
                        rating_failures[key] = error
                        delay = rating.get('retry_after_seconds', 0)
                        if type(delay) in (int, float) and isfinite(delay) and delay > 0:
                            checkpoint['card_rating_not_before'] = max(
                                checkpoint.get('card_rating_not_before', 0), time() + delay)
                    elif not local:
                        analysis_errors.append(error)
                if incremental:
                    save(check_stop=False)

        def dispatch(cards):
            with ThreadPoolExecutor(max_workers=limit, thread_name_prefix='profile-card') as workers:
                pending = {}
                try:
                    for original in cards:
                        heartbeat()
                        while len(pending) >= limit:
                            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
                            for future in ready:
                                collect(future, pending.pop(future))
                        heartbeat()
                        if individual_rating and analysis_errors:
                            raise analysis_errors[0]
                        with lock:
                            current = saved[original['candidate_id']]
                            if current.get('analysis_completed_refresh_id') != refresh_id:
                                if incremental:
                                    if analysis_retry.get(original['candidate_id'], 0) >= MAX_ATTEMPTS:
                                        details = (current.get('analysis_refresh_error') or current.get('error')
                                                   or (current.get('analysis') or {}).get('model_error')
                                                   or (current.get('review') or {}).get('error')
                                                   or {'code': 'MODEL_RETRIES_EXHAUSTED', 'message': 'Лимит попыток стадии исчерпан.'})
                                        if details['code'] not in CARD_LOCAL_ERRORS | {'MODEL_RETRIES_EXHAUSTED'}:
                                            raise CoreError(details['code'], details['message'])
                                        analysis_failures[original['candidate_id']] = CoreError(details['code'], details['message'])
                                        stage_errors['analysis'][original['candidate_id']] = details
                                        save(check_stop=False)
                                        continue
                                    analysis_retry[original['candidate_id']] = analysis_retry.get(original['candidate_id'], 0) + 1
                                projection = current.get('metrics_projection') or {}
                                card = dict(current, state='analyzing', error=None,
                                    evaluated_at=(current.get('evaluated_at') if incremental and
                                        current.get('analysis_stage_refresh_id') == refresh_id else datetime.now(timezone.utc).isoformat()),
                                    **({'rating_version': INDIVIDUAL_RATING_VERSION} if individual_rating else {}),
                                    **({'reuse_saved_matching': bool(current.get('analysis')
                                        and projection.get('dependencies', {}).get('technical') == projection.get('origin_dependencies', {}).get('technical')),
                                        'analysis_stage_refresh_id': refresh_id}
                                        if incremental else {}))
                                put(card)
                                if incremental:
                                    save(check_stop=False)
                            else:
                                card = current
                        future = (workers.submit(rate_analyzed_card, card['candidate_id']) if individual_rating else
                            workers.submit(_analyze, card, pipeline, run_id, stock, profile,
                                None, model, lock, heartbeat, state['stage_versions']))
                        pending[future] = original
                finally:
                    # Every lifecycle commits its analysis before rating. Drain peer
                    # successes after failures/Stop without restoring older copies.
                    while pending:
                        ready, _ = wait(pending, return_when=FIRST_COMPLETED)
                        for future in ready:
                            collect(future, pending.pop(future))

        dispatch(candidates)
        if incremental:
            for delay in RETRY_DELAYS:
                retry = [saved[key] for key in analysis_failures if analysis_retry.get(key, 0) < MAX_ATTEMPTS]
                if not retry or analysis_errors:
                    break
                for card in retry:
                    analysis_failures.pop(card['candidate_id'], None)
                model_retry._wait_for_retry(max(delay, checkpoint.get('card_rating_not_before', 0) - time()), heartbeat)
                dispatch(retry)
        if individual_rating:
            for delay in RETRY_DELAYS:
                retry = [saved[key] for key in rating_failures
                         if saved[key]['individual_rating'].get('attempts', 0) < MAX_ATTEMPTS]
                if not retry or analysis_errors:
                    break
                model_retry._wait_for_retry(max(delay, checkpoint.get('card_rating_not_before', 0) - time()), heartbeat)
                dispatch(retry)
            if not incremental:
                analysis_errors.extend(rating_failures.values())
        if analysis_errors:
            raise analysis_errors[0]
        if not individual_rating:
            state['phase'] = 'rating'
            progress()
            rating = pipeline.rate_run(job, dict(run, data=state), profile, stock, model=model)
            if rating and rating.get('status') in {'failed', 'unknown', 'running'}:
                error = rating.get('error') or {'code': 'RATING_NOT_COMPLETED',
                    'message': 'Сравнительная оценка не завершена.'}
                raise CoreError(error['code'], error['message'])
        if incremental:
            stage_errors['metrics'] = deepcopy(checkpoint.get('metrics_errors', {}))
            failed = set().union(*(set(errors) for errors in stage_errors.values()))
            state['completion_summary'] = {'version': 'profile-completion-v1', 'failed_tenders': len(failed),
                **{stage + '_failed': len(errors) for stage, errors in stage_errors.items()}}
        checkpoint['finished'] = True
        state.update(state='complete', phase='complete', stop_reason=checkpoint['stop_reason'])
        progress()
        profiles.finish_refresh(scope, source_kind, job['job_id'])
    except Exception as error:
        details = {'code': error.code if isinstance(error, CoreError) else 'EXECUTION_FAILED',
                   'message': error.message if isinstance(error, CoreError) else 'Не удалось обновить профиль.'}
        state.update(state='failed', phase='failed', error=details, stop_reason=details['code'])
        # Heartbeat can itself be the stop signal. Persist diagnostics directly.
        save(check_stop=False)
        profiles.finish_refresh(scope, source_kind, job['job_id'], error=details)
        raise
