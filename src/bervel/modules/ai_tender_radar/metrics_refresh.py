"""Local latest metrics from saved proofs; no source, model or document clients."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID

from bervel.core.errors import CoreError
from bervel.core.models import ActorContext, ArtifactRef
from bervel.modules.company_settings.models import production_products, stock_for_profile

from bervel.modules.ai_tender_radar.calculation import calculate_analysis, stock_coverage_for_lots
from bervel.modules.ai_tender_radar.matching import restore_matching, PositionMatches
from bervel.modules.ai_tender_radar.presentation import with_unit_prices
from bervel.modules.ai_tender_radar.production import evaluate_economics
from bervel.modules.ai_tender_radar.result_metrics import lot_matches, result_metrics
from bervel.modules.ai_tender_radar.scoring import opportunity_assessment


METRICS_VERSION = 'saved-metrics-v1'
REFRESH_VERSION = 'incremental-profile-v2'
REANALYSIS_REFRESH_VERSION = 'incremental-profile-v3'
INCREMENTAL_REFRESH_VERSIONS = frozenset({REFRESH_VERSION, REANALYSIS_REFRESH_VERSION})


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def prepare_metrics_inputs(profile: dict, stock: dict) -> dict:
    technical = [{key: row.get(key) for key in ('sku', 'product', 'unit', 'attributes')}
                 for row in sorted(stock.get('rows', []), key=lambda r: r['sku'])]
    products = production_products(profile)
    by_product = {}
    for route in profile.get('production', []):
        by_product.setdefault(route.get('product_id'), []).append(route)
    return {'technical': fingerprint(technical), 'rows': {r['sku']: r for r in stock.get('rows', [])},
            'products': products, 'routes': by_product}


def metrics_dependencies(profile: dict, stock: dict, *, skus: set | None = None, prepared: dict | None = None) -> dict:
    prepared = prepared or prepare_metrics_inputs(profile, stock)
    skus = prepared['rows'].keys() if skus is None else skus
    rows = [prepared['rows'][sku] for sku in skus if sku in prepared['rows']]
    selected_products = [{key: product.get(key) for key in ('id', 'unit', 'attributes')}
                         for sku in sorted(skus) if (product := prepared['products'].get(sku)) is not None]
    products = {product['id'] for product in selected_products}
    resources = {r['group_id']: index for index, r in enumerate(profile.get('resources', []))}
    routes = [dict({k: v for k, v in route.items() if k not in {'evidence', 'confirmed_at', 'resource_group_id'}},
                   resource=resources.get(route.get('resource_group_id')))
              for product in sorted(products) for route in prepared['routes'].get(product, [])]
    used_resources = {r['resource'] for r in routes}
    capacity = [{k: v for k, v in r.items() if k not in {'group_id', 'confirmed_at'}}
                for i, r in enumerate(profile.get('resources', [])) if i in used_resources]
    values = [{k: r.get(k) for k in ('sku', 'product', 'unit', 'attributes', 'free_quantity', 'unit_cost', 'currency')}
              for r in sorted(rows, key=lambda r: r['sku'])]
    return {'technical': prepared['technical'], 'values': fingerprint([values, selected_products, routes, capacity])}


def selected_skus(card: dict) -> set[str]:
    return {m['sku'] for line in (card.get('analysis') or {}).get('positions', [])
            for m in line.get('matches', []) if m.get('sku')}


def analysis_fingerprint(card: dict) -> str:
    tender = card.get('tender') or {}
    return fingerprint([card.get('analysis_id'), {key: tender.get(key) for key in ('positions', 'limitations', 'terms')}, card.get('analysis'),
                        card.get('analysis_settings_snapshot_id')])


def metrics_input_fingerprint(card: dict, dependencies: dict) -> str:
    return fingerprint([METRICS_VERSION, analysis_fingerprint(card), dependencies,
                        (card.get('tender') or {}).get('terms')])


def calculate_metrics_projection(card: dict, profile: dict, stock: dict, *, snapshot_id: str,
                                 now: datetime, prepared: dict | None = None) -> dict:
    dependencies = metrics_dependencies(profile, stock, skus=selected_skus(card), prepared=prepared)
    origin = card.get('_metrics_origin') or {}
    result = {'version': METRICS_VERSION, 'source_analysis_id': card.get('analysis_id'),
        'source_analysis_fingerprint': analysis_fingerprint(card), 'settings_snapshot_id': snapshot_id,
        'stock_artifact_id': profile.get('stock_artifact_id'), 'stock_sha256': profile.get('stock_sha256'),
        'calculated_at': now.isoformat(), 'dependencies': dependencies,
        'origin_dependencies': origin,
        'dependencies_changed': dependencies != origin,
        'coverage': {'percent': None, 'incomplete': True},
        'margin': {'percent': None, 'scope': 'unknown', 'known_share': None, 'currency': None, 'reasons': []},
        'lines': [], 'reasons': []}
    result['input_fingerprint'] = metrics_input_fingerprint(card, dependencies)
    tender, analysis = card.get('tender'), card.get('analysis')
    if not tender or not analysis:
        result['reasons'] = ['FULL_ANALYSIS_REQUIRED']
        return result
    reusable = (dependencies == origin and card.get('analysis_completed_refresh_id')
        and not analysis.get('model_error') and (card.get('fulfillment') or {}).get('full_economics'))
    if reusable:
        # A completed numeric stage is still valid when all its inputs match.
        # Keep its proved-lot scope and unresolved cost alternatives exactly.
        metrics = result_metrics(card)
        coverage = deepcopy(card.get('stock_coverage_by_piece')) or {
            'percent': metrics['stock_coverage_percent'], 'incomplete': metrics['stock_coverage_incomplete']}
        refreshed, plan, reasons = analysis, card['fulfillment'], []
    else:
        matching = restore_matching(tender, stock, analysis)
        if dependencies['technical'] != origin.get('technical'):
            for line in analysis.get('positions', []):
                if not line.get('matches') and line.get('stock_reason') == 'NO_COMPATIBLE_SKU':
                    matching.positions[line['id']] = PositionMatches({}, True, 'CATALOG_CHANGED_REQUIRES_ANALYSIS')
        refreshed = with_unit_prices(tender, stock, calculate_analysis(tender, stock, matching))
        lots = lot_matches(tender, lambda p: True if matching.positions[p['id']].by_sku else
            None if matching.positions[p['id']].uncertain else False)
        matched_lots = {lot for lot, matched in lots.items() if matched is True}
        coverage = stock_coverage_for_lots(tender, stock, matching, refreshed, matched_lots)
        if dependencies['technical'] != origin.get('technical') and coverage['percent'] != '100':
            coverage['incomplete'] = True
        if matching.error or not matched_lots and (not lots or None in lots.values()):
            coverage.update(percent=None, incomplete=True)
        plan = evaluate_economics(tender, stock, refreshed, profile, now=now)
        reasons = sorted({m.reason for m in matching.positions.values() if m.reason})
    opportunity = opportunity_assessment(dict(card, analysis=refreshed, fulfillment=plan), profile.get('scoring_policy'))
    result.update(coverage=coverage, margin={'percent': opportunity['margin_percent'], 'scope': opportunity['basis'],
        'known_share': opportunity['known_share'], 'currency': (plan['full_economics'] if opportunity['basis'] == 'full'
            else refreshed.get('economics') or {}).get('currency'), 'reasons': opportunity['reasons']},
        lines=[{key: line.get(key) for key in ('id', 'covered', 'shortage', 'stock_status', 'stock_reason',
            'unit_prices', 'unit_price_reasons', 'economics', 'allocations')} for line in refreshed['positions']],
        stock_economics=refreshed['economics'], full_economics=plan['full_economics'],
        analysis_coverage=refreshed.get('coverage_percent'), analysis_benefit=refreshed.get('benefit_percent'),
        reasons=reasons)
    return result


def project_metrics(card: dict) -> dict:
    projection = card.get('metrics_projection')
    if not projection or projection.get('source_analysis_fingerprint') != analysis_fingerprint(card):
        return card
    public = dict(card, analysis=deepcopy(card.get('analysis')), metrics=dict(card.get('metrics') or {}),
                  opportunity=dict(card.get('opportunity') or {}),
                  projection=dict({'version': 1, 'review_stale': False}, **(card.get('projection') or {})))
    public['metrics'].update(stock_coverage_percent=projection['coverage']['percent'],
                             stock_coverage_incomplete=projection['coverage']['incomplete'])
    public['opportunity'].update(margin_percent=projection['margin']['percent'], basis=projection['margin']['scope'],
                                  known_share=projection['margin']['known_share'])
    by_id = {line['id']: line for line in projection['lines']}
    if public.get('analysis'):
        for line in public['analysis'].get('positions', []):
            line.update({k: v for k, v in by_id.get(line['id'], {}).items() if k != 'id'})
        public['analysis']['economics'] = deepcopy(projection['stock_economics'])
        public['analysis']['coverage_percent'] = projection['analysis_coverage']
        public['analysis']['benefit_percent'] = projection['analysis_benefit']
    if card.get('fulfillment'):
        public['fulfillment'] = dict(card['fulfillment'], full_economics=deepcopy(projection['full_economics']))
    public['metrics_projection'] = {key: value for key, value in projection.items() if key != 'lines'}
    from bervel.modules.ai_tender_radar.individual_rating import _metrics, _valid_saved_result
    rating = card.get('individual_rating') or {}
    old_facts = (rating.get('input') or {}).get('facts') or {}
    facts_changed = bool(old_facts) and any(old_facts.get(key) != value for key, value in _metrics(public).items())
    if projection['dependencies_changed'] or facts_changed:
        public['projection']['metrics_review_stale'] = bool(card.get('review') or card.get('report_summary'))
        if rating.get('status') == 'complete' and _valid_saved_result(rating):
            public['rating'] = {'status': 'stale', 'score': None, 'previous_score': rating['score'],
                                'prompt_version': rating.get('prompt_version')}
    return public


def full_input_fingerprint(card: dict, dependencies: dict, *, now: datetime) -> str:
    from bervel.modules.ai_tender_radar.individual_rating import application_deadline
    from bervel.modules.ai_tender_radar.production import _first_day
    from bervel.modules.ai_tender_radar.profile_policy import MOSCOW
    tender = card.get('tender') or {}
    deadline = application_deadline(card)
    return fingerprint([dependencies, {key: tender.get(key) for key in ('positions', 'terms', 'limitations', 'documents')},
        deadline.isoformat() if deadline else None, _first_day(now.astimezone(MOSCOW)).isoformat()])


def refresh_metrics_cards(pipeline, job: dict, run: dict, profile: dict, stock: dict,
                          checkpoint: dict, save) -> None:
    """Local queue stage; persistence and frozen-input access use existing ports."""
    run_id = UUID(str(run['run_id']))
    target = str(job['operation']['settings_snapshot_id'])
    prepared = prepare_metrics_inputs(profile, stock)
    cache = {target: (profile, stock, prepared)}
    cards = [row['data'] for row in pipeline.repository.cards(run_id)
             if row['data'].get('analysis') and row['data'].get('tender')]
    done = checkpoint.setdefault('metrics_done', {})
    checkpoint.update(metrics_total=len(cards), metrics_completed=len(done))
    failures = {}
    now = datetime.now(timezone.utc)
    for card in cards:
        pipeline.jobs.heartbeat(job['job_id'])
        skus = selected_skus(card)
        origin = card.get('analysis_metrics_dependencies')
        if origin is None:
            original_id = card.get('analysis_settings_snapshot_id') or target
            if original_id not in cache:
                original = pipeline.settings.read_snapshot(job['data_scope'], UUID(original_id))['profile']
                actor = ActorContext(job['actor_id'], job['data_scope'], frozenset({'artifact:read'}))
                artifact = pipeline.artifacts.read(actor, ArtifactRef(UUID(original['stock_artifact_id'])))
                if artifact.metadata['sha256'] != original['stock_sha256']:
                    raise CoreError('STALE_REVISION', 'Исходный склад анализа не совпадает с его снимком.')
                original_stock = stock_for_profile(pipeline.parse_stock(artifact.content), original,
                    expose_experimental_sku=True)
                cache[original_id] = (original, original_stock, prepare_metrics_inputs(original, original_stock))
            old_profile, old_stock, old_prepared = cache[original_id]
            origin = metrics_dependencies(old_profile, old_stock, skus=skus, prepared=old_prepared)
        candidate = dict(card, _metrics_origin=origin)
        dependencies = metrics_dependencies(profile, stock, skus=skus, prepared=prepared)
        expected = metrics_input_fingerprint(card, dependencies)
        logical_refresh = job['operation'].get('refresh_id') or str(job['job_id'])
        if (job['operation'].get('kind') == 'profile_refresh'
                and card.get('analysis_completed_refresh_id') == logical_refresh):
            # The completed full stage already superseded the numerical prelude.
            # A resume must not replace its verified view with a new projection.
            done[card['candidate_id']] = expected
            checkpoint['metrics_completed'] = len(done)
            continue
        if done.get(card['candidate_id']) == expected:
            continue
        previous = card.get('metrics_projection') or {}
        if previous.get('input_fingerprint') != expected:
            try:
                result = calculate_metrics_projection(candidate, profile, stock, snapshot_id=target,
                                                      now=now, prepared=prepared)
            except CoreError as error:
                if error.code not in {'VALIDATION_ERROR', 'METRICS_CARD_FAILED'}:
                    raise
                failures[card['candidate_id']] = {'code': error.code, 'message': error.message}
                continue
            # Frozen input access and persistence failures abort the execution.
            pipeline.repository.upsert_card(run_id, card['candidate_id'], {'metrics_projection': result})
        done[card['candidate_id']] = expected
        checkpoint['metrics_completed'] = len(done)
        save()
    checkpoint['metrics_errors'] = failures
    save()
    if failures:
        raise CoreError('METRICS_PARTIAL_FAILURE', f'Показатели {len(failures)} карточек не обновлены. Готовые результаты сохранены.')
