"""Finite, quota-aware named runs using the existing card analysis stages."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from threading import RLock
from uuid import UUID, uuid4

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef
from bervel.modules.ai_tender_radar.brief_ranking import AI_SELECTION_VERSION, CARD_LOCAL_ERRORS, rank_briefs_with_model
from bervel.modules.ai_tender_radar.calculation import calculate_analysis, stock_coverage_for_lots
from bervel.modules.ai_tender_radar.completeness import check_completeness
from bervel.modules.ai_tender_radar.documents import LEGACY_PROMPT_VERSION as LEGACY_DOCUMENT_PROMPT_VERSION
from bervel.modules.ai_tender_radar.matching import match_positions, restore_matching
from bervel.modules.ai_tender_radar.models import USER_STOPPED
from bervel.modules.ai_tender_radar.prefilter import (
    CANDIDATE_PRIORITY_VERSION, CatalogPrefilter, LOT_COVERAGE_SELECTION_VERSION, prioritize_mentions,
)
from bervel.modules.ai_tender_radar.result_metrics import catalog_percent, lot_matches
from bervel.modules.ai_tender_radar.review import ANALYTICAL_PROMPT_VERSION, COMPACT_PROMPT_VERSION
from bervel.modules.ai_tender_radar.presentation import present_card
from bervel.modules.ai_tender_radar.production import evaluate_fulfillment
from bervel.modules.ai_tender_radar.scoring import score_assessment
from bervel.modules.ai_tender_radar.selection import collect_candidates, model_search_queries, require_simulator, search_queries_v3
from bervel.modules.ai_tender_radar.source_contract import PURCHASE_STATUS, brief_is_closed


_CALCULATION_FIELDS = ('analysis', 'fulfillment', 'assessment', 'completeness', 'catalog_match_percent',
    'stock_coverage_by_piece', 'selection', 'state', 'evaluated_at', 'opportunity', 'report_summary', 'projection')


def _save_analysis_stage(pipeline, run_id, card, lock, heartbeat):
    # The production pipeline binds this write to the current job and live lease.
    # No model call runs while the shared run lock is held.
    with lock:
        heartbeat()
        writer = getattr(pipeline.repository, 'upsert_card', None)
        if writer is None:
            writer = pipeline.profiles.repository.upsert_card
        writer(run_id, card['candidate_id'], card)


def _stage_inputs(card, stock, profile, versions, pipeline, *, analysis=None):
    from bervel.modules.ai_tender_radar.metrics_refresh import (
        fingerprint, full_input_fingerprint, metrics_dependencies, prepare_metrics_inputs, selected_skus,
        REANALYSIS_REFRESH_VERSION,
    )
    prepared = getattr(pipeline, 'metrics_prepared', None) or prepare_metrics_inputs(profile, stock)
    tender = card['tender']
    positions = [{key: p.get(key) for key in ('id', 'lot', 'name', 'unit', 'characteristics')}
                 for p in tender.get('positions', [])]
    matching = fingerprint([versions.get('matching'), versions.get('selection'), prepared['technical'],
                            positions, tender.get('limitations')])
    proof = analysis if analysis is not None else (
        ((card.get('analysis_stage_cache') or {}).get('calculation') or {}).get('analysis') or card.get('analysis'))
    dependencies = metrics_dependencies(profile, stock, skus=selected_skus(dict(card, analysis=proof)), prepared=prepared)
    # The full catalog's technical digest is prepared once per job. Numerical
    # dependencies only visit proved SKUs; never hash the whole stock per card.
    stamp = datetime.fromisoformat(card['evaluated_at'])
    inputs = [matching, full_input_fingerprint(card, dependencies, now=stamp),
              versions.get('production'), versions.get('score'), profile.get('scoring_policy')]
    if versions.get('refresh') == REANALYSIS_REFRESH_VERSION:
        inputs.append(card.get('analysis_stage_refresh_id'))
    calculation = fingerprint(inputs)
    return matching, calculation


def _finish_stage_review(card, pipeline, run_id, stock, profile, model, lock, heartbeat, versions, cache, *, projected=False):
    from bervel.modules.ai_tender_radar.metrics_refresh import fingerprint
    from bervel.modules.ai_tender_radar.review import review_card
    review_version = versions.get('review')
    if review_version == ANALYTICAL_PROMPT_VERSION and not projected:
        card = present_card(card, profile, stock)
    cache['calculation'] = {key: deepcopy(card[key]) for key in _CALCULATION_FIELDS if key in card}
    # Persist every completed matching/calculation result before review I/O.
    card['analysis_stage_cache'] = cache
    card['review'] = None
    _save_analysis_stage(pipeline, run_id, card, lock, heartbeat)
    review_input = fingerprint([cache['input_fingerprint'], review_version, card.get('brief'),
                                card.get('report_summary')])
    previous = cache.get('review_result') or {}
    if cache.get('review_input_fingerprint') == review_input and previous.get('state') == 'complete':
        card['review'] = deepcopy(previous)
        return card
    result = card.get('state')
    confirmed = bool((card.get('selection') or {}).get('confirmed_matching'))
    full_catalog_fit = card.get('catalog_match_percent') == '100'
    if review_version in {COMPACT_PROMPT_VERSION, ANALYTICAL_PROMPT_VERSION} or result == 'accepted' or (full_catalog_fit and confirmed):
        card['review'] = review_card(card, model, f'review-{card["analysis_id"]}', heartbeat,
            prompt_version=review_version,
            **({'stock': stock, 'profile': profile} if review_version == ANALYTICAL_PROMPT_VERSION else {}))
    cache['review_input_fingerprint'] = review_input
    if (card.get('review') or {}).get('state') == 'complete':
        cache['review_result'] = deepcopy(card['review'])
        _save_analysis_stage(pipeline, run_id, card, lock, heartbeat)
    return card


class _CountedModel:
    def __init__(self, model, state, lock, save, *, before_generate=None):
        self.model, self.state, self.lock, self.save = model, state, lock, save
        self.before_generate = before_generate

    def generate(self, *args, **kwargs):
        # Persist before sending, including calls with unknown outcomes.
        try:
            if self.before_generate is not None:
                self.before_generate()
            with self.lock:
                self.state['model_attempts'] = self.state.get('model_attempts', 0) + 1
                self.save()
        except CoreError:
            raise
        except Exception as error:
            # The request has not been sent. A failed reservation is not an
            # unknown provider response, even inside rate_card's model boundary.
            raise CoreError('PERSISTENCE_ERROR', 'Не удалось сохранить состояние обновления.') from error
        return self.model.generate(*args, **kwargs)


def _requested_search_limit(groups: dict[str, dict], detail_max: int) -> int:
    search_total, detail_total = (groups[group].get('TotalLimit') for group in ('search', 'purchase'))
    if type(search_total) is not int or search_total < 0 or type(detail_total) is not int or detail_total < 0:
        raise CoreError('KONTUR_INVALID_RESPONSE', 'Источник не сообщил максимумы квот поиска и карточек.')
    return max(1, detail_max * search_total // detail_total) if search_total and detail_total else 0


def _run_budgets(limits: list[dict], used: dict, unknown: dict, detail_max: int | None) -> dict:
    groups = {entry.get('LimitGroup'): entry for entry in limits}
    remaining = {group: groups.get(group, {}).get('AvailableRequests') for group in ('search', 'purchase')}
    if any(type(value) is not int or value < 0 for value in remaining.values()):
        raise CoreError('KONTUR_INVALID_RESPONSE', 'Источник не сообщил отдельные квоты поиска и карточек.')
    budgets = {
        'search': used['search'] + max(0, remaining['search'] - unknown['search']),
        'detail': used['detail'] + max(0, remaining['purchase'] - unknown['detail']),
    }
    if detail_max is not None:
        budgets['search'] = min(budgets['search'], _requested_search_limit(groups, detail_max))
        budgets['detail'] = min(budgets['detail'], detail_max)
    return budgets


def _analyze(card: dict, pipeline, run_id: UUID, stock: dict, profile: dict,
             prefilter: CatalogPrefilter | None, model, lock, heartbeat, versions: dict) -> dict:
    """A worker owns one card; shared repository/state writes use the run lock."""
    card = deepcopy(card)
    candidate_id = card['candidate_id']
    incremental = versions.get('refresh') in {'incremental-profile-v2', 'incremental-profile-v3'}
    reuse_saved_matching = card.pop('reuse_saved_matching', False)
    cache = deepcopy(card.get('analysis_stage_cache') or {}) if incremental else {}
    if incremental:
        card.pop('error', None)
    try:
        heartbeat()
        tender = card['tender']
        ai_selected = versions.get('selection') == AI_SELECTION_VERSION
        priority = versions.get('selection') == CANDIDATE_PRIORITY_VERSION
        selected_lot_coverage = ai_selected or priority
        use_lot_coverage = selected_lot_coverage or versions.get('selection') == LOT_COVERAGE_SELECTION_VERSION
        evaluate_catalog_fit = use_lot_coverage or versions.get('selection') == 'catalog-prefilter-v4'
        declared_docs = (tender.get('source_card') or {}).get('Docs')
        if not ai_selected:
            assert prefilter is not None
            with lock:
                raw_decision = prefilter.check(card.get('raw_tender') or tender)
                if raw_decision['state'] == 'reject':
                    raw_lots = lot_matches(tender, prefilter.catalog_match)
                    card['catalog_match_percent'] = catalog_percent(raw_lots)
            if raw_decision['state'] == 'reject' and not (priority and (tender.get('documents') or declared_docs)):
                has_matching_lot = use_lot_coverage and True in raw_lots.values()
                if not has_matching_lot and not (evaluate_catalog_fit and card['catalog_match_percent'] == '100'):
                    card.update(state='irrelevant', analysis=None, fulfillment=None, assessment=None, review=None,
                                completeness=check_completeness(tender), selection=raw_decision)
                    return card
        if incremental:
            from bervel.modules.ai_tender_radar.metrics_refresh import fingerprint
        document_input = fingerprint([versions.get('documents'), tender.get('documents'), declared_docs,
                                      (card.get('raw_tender') or tender).get('positions')]) if incremental else None
        if not card.get('input_finalized') or (incremental and card.get('document_input_fingerprint') != document_input):
            if tender.get('documents') or (selected_lot_coverage and declared_docs):
                from bervel.modules.ai_tender_radar.documents import enrich_tender
                tender = enrich_tender(tender, model, f'documents-{card["analysis_id"]}', heartbeat,
                    profile=profile, prompt_version=versions.get('documents', LEGACY_DOCUMENT_PROMPT_VERSION))
                if incremental:
                    failure = (tender.get('document_extraction') or {}).get('error') or {}
                    if failure.get('code', '').startswith('MODEL_') or failure.get('code') in {'USER_STOPPED', 'AI_RADAR_JOB_INTERRUPTED'}:
                        card.update(tender=tender, input_finalized=False)
                        raise CoreError(failure['code'], failure['message'])
            card.update(tender=tender, input_finalized=True)
            with lock:
                heartbeat()
                pipeline.repository.prepare_card(run_id, candidate_id, tender)
            if incremental:
                card['document_input_fingerprint'] = fingerprint([versions.get('documents'), tender.get('documents'),
                    (tender.get('source_card') or {}).get('Docs'), (card.get('raw_tender') or tender).get('positions')])
                _save_analysis_stage(pipeline, run_id, card, lock, heartbeat)
        matching_input, calculation_input = _stage_inputs(card, stock, profile, versions, pipeline) if incremental else (None, None)
        if incremental and cache.get('input_fingerprint') == calculation_input and cache.get('calculation'):
            card.update(deepcopy(cache['calculation']))
            return _finish_stage_review(card, pipeline, run_id, stock, profile, model, lock, heartbeat,
                                        versions, cache, projected=True)
        restored = None
        saved_analysis = (cache.get('calculation') or {}).get('analysis') or card.get('analysis')
        if (incremental and saved_analysis and not saved_analysis.get('model_error')
                and (cache.get('matching_input_fingerprint') == matching_input
                     or reuse_saved_matching and not cache.get('matching_input_fingerprint'))):
            candidate = restore_matching(tender, stock, saved_analysis)
            if not any(match.uncertain for match in candidate.positions.values()):
                # Legacy negative decisions have no saved requirements fingerprint.
                if cache.get('matching_input_fingerprint') == matching_input or all(
                        match.by_sku for match in candidate.positions.values()):
                    restored = candidate
        completeness = check_completeness(tender)
        if ai_selected:
            matching = restored or match_positions(tender, stock, model, f'run-{run_id}-{candidate_id}', heartbeat)

            def verified_match(position):
                matched = matching.positions[position['id']]
                return True if matched.by_sku else None if matched.uncertain else False

            catalog_lots = lot_matches(tender, verified_match)
            outcomes = catalog_lots.values()
            decision = {'state': 'candidate' if True in outcomes else 'unknown' if not outcomes or None in outcomes
                        else 'reject', 'upper_coverage_percent': None}
        else:
            assert prefilter is not None
            with lock:
                decision = prefilter.check(tender)
                catalog_lots = lot_matches(tender, prefilter.catalog_match)
        card.update(completeness=completeness, catalog_match_percent=catalog_percent(catalog_lots))
        full_catalog_fit = evaluate_catalog_fit and card['catalog_match_percent'] == '100'
        matched_lots = {lot for lot, matched in catalog_lots.items() if matched is True}
        if priority and catalog_lots and all(matched is False for matched in catalog_lots.values()):
            card.update(state='irrelevant', analysis=None, fulfillment=None, assessment=None, review=None,
                        selection={**decision, 'reason': 'CATALOG_MATCH_NOT_CONFIRMED'})
            return card
        if not ai_selected and decision['state'] == 'reject' and not full_catalog_fit and not (use_lot_coverage and matched_lots):
            # An optimistic bound is not an analysis result or a coverage estimate.
            card.update(state='irrelevant', analysis=None, fulfillment=None, assessment=None, review=None,
                        selection=decision)
            return card
        if not ai_selected:
            matching = restored or match_positions(tender, stock, model, f'run-{run_id}-{candidate_id}', heartbeat)
        analysis = calculate_analysis(tender, stock, matching)
        if use_lot_coverage:
            card['stock_coverage_by_piece'] = ({'percent': None, 'incomplete': True}
                if ai_selected and decision['state'] == 'unknown' else
                stock_coverage_for_lots(tender, stock, matching, analysis, matched_lots))
        plan = evaluate_fulfillment(tender, stock, analysis, profile,
            now=datetime.fromisoformat(card['evaluated_at']), rule_version=versions.get('production', 2))
        assessment = score_assessment(tender, plan, profile.get('scoring_policy'))
        coverage = Decimal(analysis['coverage_percent']) if analysis.get('coverage_percent') is not None else None
        confirmed = any(position.get('matches') for position in analysis['positions'])
        uncertain = bool(analysis.get('model_error')) or any(
            position.get('stock_status') == 'unconfirmed' for position in analysis['positions'])
        if selected_lot_coverage and matched_lots:
            lot_coverage = card['stock_coverage_by_piece']
            coverage = None if lot_coverage['incomplete'] else Decimal(lot_coverage['percent'])
            relevant = [position for position in analysis['positions'] if position['lot'] in matched_lots]
            confirmed = bool(relevant) and all(position.get('matches') for position in relevant)
            uncertain = bool(analysis.get('model_error')) or any(
                position['stock_status'] == 'unconfirmed' for position in relevant)
        if ai_selected and decision['state'] == 'reject':
            result, reason = 'irrelevant', 'CATALOG_MATCH_NOT_CONFIRMED'
        elif ai_selected and decision['state'] == 'unknown':
            result, reason = 'needs_data', 'ANALYSIS_REQUIRES_DATA'
        elif coverage is not None and coverage < 80 and not full_catalog_fit and not (selected_lot_coverage and matched_lots):
            result, reason = 'irrelevant', 'ACTUAL_STOCK_COVERAGE_BELOW_80'
        elif not confirmed and not uncertain:
            result, reason = 'irrelevant', 'CATALOG_MATCH_NOT_CONFIRMED'
        elif coverage is None or not confirmed or uncertain or not completeness['complete']:
            result, reason = 'needs_data', 'ANALYSIS_REQUIRES_DATA'
        else:
            result, reason = 'accepted', 'FULL_CATALOG_MATCH' if full_catalog_fit else (
                'CATALOG_LOTS_CONFIRMED' if selected_lot_coverage and matched_lots else 'ACTUAL_STOCK_COVERAGE_CONFIRMED')
        card.update(analysis=analysis, fulfillment=plan, assessment=assessment, state=result,
            selection={**decision, 'reason': reason, 'confirmed_matching': confirmed})
        if incremental and not analysis.get('model_error'):
            matching_input, calculation_input = _stage_inputs(card, stock, profile, versions, pipeline,
                                                              analysis=card['analysis'])
            cache.update(matching_input_fingerprint=matching_input, input_fingerprint=calculation_input)
            return _finish_stage_review(card, pipeline, run_id, stock, profile, model, lock, heartbeat, versions, cache)
        review_version = versions.get('review')
        if review_version in {COMPACT_PROMPT_VERSION, ANALYTICAL_PROMPT_VERSION} or result == 'accepted' or (full_catalog_fit and confirmed):
            from bervel.modules.ai_tender_radar.review import review_card
            if review_version == ANALYTICAL_PROMPT_VERSION:
                card = present_card(card, profile, stock)
            card['review'] = review_card(card, model, f'review-{card["analysis_id"]}', heartbeat,
                                         prompt_version=review_version,
                                         **({'stock': stock, 'profile': profile} if review_version == ANALYTICAL_PROMPT_VERSION else {}))
    except CoreError as error:
        if error.code == USER_STOPPED or incremental and error.code not in CARD_LOCAL_ERRORS:
            raise
        card.update(state='failed', error={'code': error.code, 'message': error.message})
    return card


def execute_named(pipeline, job: dict, run: dict, profile: dict, stock: dict) -> None:
    """Execute selection-v3 against only the explicitly selected source adapter.

    The caller validates immutable profile/stock inputs. Source I/O is serial;
    at most four cards may be submitted for model/analysis work at one time.
    """
    actor = ActorContext(job['actor_id'], job['data_scope'], frozenset({'artifact:read', 'process:read'}))
    run_id = UUID(str(run.get('run_id') or job['operation'].get('run_id') or job['job_id']))
    repository, lock = pipeline.repository, RLock()
    state = deepcopy(run['data'])
    state.update(state='running', current_job_id=str(job['job_id']))
    state.pop('error', None)
    state.setdefault('stage_versions', {'selection': 'catalog-prefilter-v3', 'normalization': 'kontur-v2',
        'matching': 'verified-sku-2026-11-05', 'documents': 'document-extraction-v1', 'production': 2,
        'score': 'score-v1', 'review': 'card-review-v1', 'audit': 'run-audit-v1'})
    state.setdefault('model_attempts', 0)
    state.setdefault('detail_attempts', 0)
    strategy = state['stage_versions'].get('selection')
    priority = strategy == CANDIDATE_PRIORITY_VERSION
    ai_selection = strategy == AI_SELECTION_VERSION
    saved = {row['candidate_id']: row['data'] for row in repository.cards(run_id)}

    def heartbeat():
        with lock:
            pipeline.jobs.heartbeat(job['job_id'])

    def save():
        with lock:
            heartbeat()
            repository.update(actor.data_scope, run_id, state)

    def progress():
        candidates = state.get('ranked_candidates', state.get('checkpoint', {}).get('candidates', []))
        finished = {key for key, card in saved.items() if card.get('state') != 'analyzing'}
        checked = {key for key, card in saved.items()
                   if card.get('state') in {'accepted', 'needs_data', 'irrelevant'}}
        state['counts'] = {kind: sum(card.get('state') == kind for card in saved.values())
                           for kind in ('accepted', 'needs_data', 'irrelevant', 'failed')}
        state['counts']['looked'] = len(candidates)
        state['processed'] = len(finished)
        state['unverified_count'] = sum(candidate['id'] not in checked for candidate in candidates)
        state['scope_complete'] = (state.get('checkpoint', {}).get('stop_reason') == 'search_exhausted'
                                   and state['unverified_count'] == 0)
        save()

    def checkpoint(value):
        with lock:
            state['checkpoint'] = value
            progress()

    def archive_page(page):
        with lock:
            return repository.archive_page(run_id, page)

    def finish(future, card):
        try:
            result = future.result()
        except Exception:
            # Keep the durable raw input and expose an infrastructure failure.
            result = dict(card, state='failed', error={
                'code': 'CARD_ANALYSIS_FAILED', 'message': 'Не удалось завершить разбор карточки.'})
            with lock:
                heartbeat()
                repository.finalize_card(run_id, card['candidate_id'], result)
                saved[card['candidate_id']] = result
                progress()
            raise
        with lock:
            heartbeat()
            repository.finalize_card(run_id, card['candidate_id'], result)
            saved[card['candidate_id']] = result
            progress()

    try:
        heartbeat()
        model = _CountedModel(pipeline.model, state, lock, save)
        source_kind = state.get('source')
        purchase_stages = state.get('purchase_stages')
        detail_max = (state.get('budgets') or {}).get('detail')
        source, file_cards = None, {}
        pinned = deepcopy(state.get('checkpoint', {}))
        pinned.setdefault('search_start', '1970-01-01T00:00:00Z')
        started = run.get('created_at') or datetime.now(timezone.utc)
        pinned.setdefault('search_end', started.isoformat() if isinstance(started, datetime) else str(started))
        state['checkpoint'] = pinned
        state['search_scope'] = {'from': pinned['search_start'], 'to': pinned['search_end'],
                                 'source': source_kind, 'active_only': not purchase_stages or purchase_stages == ['open']}
        if purchase_stages:
            state['search_scope']['purchase_stages'] = purchase_stages
        if source_kind == 'file':
            if pipeline.parse_archive is None:
                raise CoreError('FILE_SOURCE_NOT_CONFIGURED', 'Парсер файла закупок не настроен.')
            artifact = pipeline.artifacts.read(actor, ArtifactRef(UUID(state['source_artifact_id'])))
            if artifact.metadata['sha256'] != state.get('source_sha256'):
                raise CoreError('STALE_REVISION', 'Файл закупок отличается от сохранённого снимка прогона.')
            file_cards = {tender['id']: tender for tender in pipeline.parse_archive(artifact.content)}
            pinned.setdefault('candidates', [{'id': tender['id'], 'brief': {'Id': tender['id'],
                'OrderName': tender['title']}, 'state': 'candidate'} for tender in file_cards.values()])
            pinned.update(stop_reason='search_exhausted')
            state['checkpoint'] = pinned
            state['effective_budgets'] = {'search': 0, 'detail': detail_max}
        else:
            source = pipeline.sources.get(source_kind)
            if source_kind not in {'test_api', 'kontur_api'} or source is None or not getattr(source, 'key', None):
                raise CoreError('KONTUR_SOURCE_NOT_CONFIGURED', 'Выбранный источник закупок не настроен.')
            if source_kind == 'test_api':
                require_simulator(source)
            manual_resume = job['operation']['kind'] == 'resume'
            epochs = state.setdefault('quota_epochs', [])
            # Resumed simulator requests must use the current provider period and local balance.
            refresh_quota = (source_kind == 'test_api' and getattr(source, 'provider_limits', None) is not None
                             or manual_resume and not any(epoch['job_id'] == str(job['job_id']) for epoch in epochs))
            if 'limits' not in state or refresh_quota:
                limits = source.read_limits()
                state['limits'] = limits
                used = {'search': pinned.get('search_attempts', 0), 'detail': state['detail_attempts']}
                # An interrupted request may still be charged by the provider.
                # Reserve it conservatively in addition to retaining its attempt.
                unknown = {'search': int(bool(pinned.get('pending_search'))),
                           'detail': int(bool(state.get('pending_detail')))}
                state['effective_budgets'] = (
                    {'search': detail_max, 'detail': detail_max} if source_kind == 'test_api' and not limits
                    else _run_budgets(limits, used, unknown, detail_max))
                epochs.append({'job_id': str(job['job_id']), 'read_at': datetime.now(timezone.utc).isoformat(),
                    'limits': deepcopy(limits), 'used': used, 'unknown': unknown,
                    'allowed_total': deepcopy(state['effective_budgets'])})
                save()
            search_max = state['effective_budgets']['search']
            resume_search = (manual_resume and state.get('stop_reason') == 'search_budget'
                             and (search_max is None or pinned.get('search_attempts', 0) < search_max))
            if not ai_selection or 'ranked_candidates' not in state or resume_search:
                state['phase'] = 'search'
                save()
                if ai_selection:
                    keywords = pinned.get('search_keywords')
                    if keywords is None and search_max != 0:
                        keywords = model_search_queries(profile, model, str(job['job_id']), heartbeat=heartbeat)
                        pinned['search_keywords'] = keywords
                        save()
                else:
                    keywords = search_queries_v3(profile)
                if ai_selection and not keywords:
                    pinned['stop_reason'] = 'search_budget'
                else:
                    state['checkpoint'] = collect_candidates(source, keywords,
                        search_max=search_max, checkpoint=pinned, progress=checkpoint,
                        archive_page=archive_page, allow_configured_api=source_kind == 'kontur_api',
                        purchase_statuses=[PURCHASE_STATUS[stage] for stage in purchase_stages] if purchase_stages else None,
                        **({'combined': True} if ai_selection else {}))
        candidates = state['checkpoint'].get('candidates', [])
        prefilter = None
        if ai_selection:
            previous_ranking = state.get('ranked_candidates', [])
            ranked_ids = {row['id'] for row in previous_ranking}
            unranked = [row for row in candidates if row['id'] not in ranked_ids]
            if unranked or 'ranked_candidates' not in state:
                state['phase'] = 'ranking'
                save()
                stage_candidates = [
                    dict(row, state='closed', reasons=['CLOSED_PROCEDURE'])
                    if brief_is_closed(row['brief'], purchase_stages) else row for row in unranked
                ]
                ranked = rank_briefs_with_model(stage_candidates, profile, model, str(job['job_id']),
                                                heartbeat=heartbeat)
                state['ranked_candidates'] = sorted([*previous_ranking, *ranked], key=lambda row: (
                    row.get('state') == 'closed', -row.get('rank', 0), row['id'])) if previous_ranking else ranked
        else:
            prefilter = CatalogPrefilter(stock, profile)
            ranked = prefilter.rank(candidates, strategy=strategy, purchase_stages=purchase_stages)
            state['ranked_candidates'] = prioritize_mentions(ranked) if priority else ranked
        state['phase'] = 'details'
        progress()
        source_error = None
        pending = {}
        detail_limit = state['effective_budgets']['detail']
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='radar-card') as workers:
            try:
                for candidate in state.get('ranked_candidates', []):
                    key = candidate['id']
                    card = deepcopy(saved.get(key))
                    if (card is not None and card.get('state') == 'failed'
                            and job['operation']['kind'] == 'resume' and card.get('raw_tender')):
                        heartbeat()
                        retried = repository.retry_failed_card(run_id, key, str(job['job_id']))
                        if retried is not None:
                            card = retried
                            saved[key] = card
                    if card is not None and card.get('state') != 'analyzing':
                        continue
                    while len(pending) >= 4:
                        completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                        for future in completed:
                            finish(future, pending.pop(future))
                    if candidate.get('state') == 'closed' or (
                            ai_selection and card is None and brief_is_closed(candidate['brief'], purchase_stages)):
                        if card is None:
                            card = {'analysis_id': str(uuid4()), 'candidate_id': key, 'brief': candidate['brief'],
                                'evaluated_at': datetime.now(timezone.utc).isoformat(),
                                'state': 'irrelevant', 'raw_tender': None, 'tender': None, 'analysis': None,
                                'fulfillment': None, 'assessment': None, 'review': None,
                                'selection': {'reason': 'CLOSED_PROCEDURE'}}
                            with lock:
                                heartbeat()
                                repository.add_card(run_id, key, card)
                                saved[key] = card
                                progress()
                        continue
                    if card is None:
                        if detail_limit is not None and state['detail_attempts'] >= detail_limit:
                            # Saved raw inputs later in the ranking use no source
                            # quota and must still finish on an explicit resume.
                            continue
                        with lock:
                            state['detail_attempts'] += 1
                            state['pending_detail'] = key
                            save()
                        card = {'analysis_id': str(uuid4()), 'candidate_id': key, 'brief': candidate['brief'],
                            'evaluated_at': datetime.now(timezone.utc).isoformat(), 'state': 'analyzing',
                            'raw_tender': None, 'tender': None, 'analysis': None,
                            'fulfillment': None, 'assessment': None, 'review': None}
                        if purchase_stages:
                            card['purchase_stages'] = purchase_stages
                        try:
                            fetch_options = {'allow_cancelled': True} if 'cancelled' in (purchase_stages or []) else {}
                            value = file_cards[key] if source_kind == 'file' else source.fetch_tender(
                                key, search_item=candidate['brief'], **fetch_options)
                            card.update(raw_tender=deepcopy(value), tender=value)
                        except CoreError as error:
                            if error.code != 'KONTUR_TENDER_CLOSED':
                                raise
                            card.update(state='irrelevant', selection={'reason': 'CLOSED_PROCEDURE'})
                        with lock:
                            heartbeat()
                            repository.add_card(run_id, key, card)
                            saved[key] = card
                            state['pending_detail'] = None
                            progress()
                        if card['state'] == 'irrelevant':
                            continue
                    pending[workers.submit(_analyze, card, pipeline, run_id, stock, profile,
                                           prefilter, model, lock, heartbeat, state['stage_versions'])] = card
            except Exception as error:
                source_error = error
            # Commit every finished card even when a later serial source call fails.
            while pending:
                completed, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in completed:
                    try:
                        finish(future, pending.pop(future))
                    except Exception as error:
                        source_error = source_error or error
        if source_error is not None:
            raise source_error
        progress()
        search_stop = state['checkpoint']['stop_reason']
        if state['scope_complete']:
            stop = 'search_exhausted'
        elif state['counts']['failed']:
            stop = 'card_failures'
        elif state['unverified_count'] and detail_limit is not None and state['detail_attempts'] >= detail_limit:
            stop = 'requested_detail_limit' if detail_max == detail_limit else 'detail_quota'
        elif search_stop == 'search_budget' and detail_max is not None and state['checkpoint']['search_attempts'] >= (
                _requested_search_limit({row['LimitGroup']: row for row in state['limits']}, detail_max)
                if state['limits'] else detail_max):
            stop = 'requested_search_limit'
        else:
            stop = search_stop
        complete = stop in {'search_exhausted', 'requested_detail_limit', 'requested_search_limit'}
        state.update(state='complete' if complete else 'failed', phase='complete', stop_reason=stop)
        progress()
    except Exception as error:
        if isinstance(error, CoreError) and error.code == USER_STOPPED:
            raise
        state.update(state='failed', stop_reason=error.code if isinstance(error, CoreError) else 'EXECUTION_FAILED')
        if isinstance(error, CoreError):
            state['error'] = {'code': error.code, 'message': error.message}
        progress()
        raise
