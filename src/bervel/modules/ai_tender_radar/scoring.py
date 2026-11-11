"""Deterministic scoring for one complete, verified fulfillment plan."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from bervel.modules.ai_tender_radar.calculation import _price
from bervel.modules.company_settings.policy import effective_scoring_policy


LEGACY_DEFAULT_POLICY = {'version': 'score-v1', 'target_margin': '0.30', 'comfortable_utilization': '0.70',
                         'minimum_production_factor': '0.60', 'high_score': 80, 'medium_score': 50,
                         'stress_price_factor': '0.90', 'stress_cost_factor': '1.10', 'stress_rate_factor': '0.80'}


def _number(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() else None


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _score(plan: dict, policy: dict) -> dict:
    reasons = list(plan.get('reasons', []))
    result = {'score': None, 'E': None, 'S': plan.get('stock_share'), 'P': None, 'reasons': reasons}
    if plan.get('status') == 'infeasible':
        return {**result, 'score': 0, 'reasons': reasons + ['FULFILLMENT_INFEASIBLE']}
    economics = plan.get('full_economics') or {}
    revenue, cost = _number(economics.get('revenue')), _number(economics.get('cost'))
    complete = economics.get('scope') == 'full' and bool(economics.get('currency'))
    if complete and revenue is not None and revenue > 0 and cost is not None and cost >= 0 and revenue - cost <= 0:
        return {**result, 'score': 0, 'reasons': reasons + ['PLAN_NOT_PROFITABLE']}
    if plan.get('version', 1) >= 4 and complete and plan.get('status') != 'feasible':
        return {**result, 'reasons': reasons + ['FULFILLMENT_UNCONFIRMED']}
    if plan.get('status') != 'feasible' or not complete or revenue is None or revenue <= 0 or cost is None or cost < 0:
        return {**result, 'reasons': reasons + ['FULL_ECONOMICS_OR_FEASIBILITY_UNKNOWN']}
    share, utilization = _number(plan.get('stock_share')), _number(plan.get('utilization'))
    target = _number(policy.get('target_margin'))
    comfortable = _number(policy.get('comfortable_utilization'))
    minimum = _number(policy.get('minimum_production_factor'))
    if (share is None or not 0 <= share <= 1 or (share < 1 and (utilization is None or not 0 <= utilization <= 1))
            or target is None or target <= 0 or comfortable is None or not 0 <= comfortable < 1
            or minimum is None or not 0 <= minimum <= 1):
        return {**result, 'reasons': reasons + ['SCORING_INPUT_UNKNOWN']}
    economic = min(Decimal(1), max(Decimal(0), ((revenue - cost) / revenue) / target))
    pressure = Decimal(0) if share == 1 else min(Decimal(1), max(Decimal(0), (utilization - comfortable) / (1 - comfortable)))
    production = 1 - (1 - minimum) * pressure
    score = (100 * economic * (share + (1 - share) * production)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return {**result, 'score': int(score), 'E': _text(economic), 'S': _text(share), 'P': _text(production)}


def score_assessment(tender: dict, plan: dict, policy: dict | None = None) -> dict:
    """Historical plans retain their original score and stress assessment."""
    legacy = plan.get('version', 1) < 3
    effective = {**LEGACY_DEFAULT_POLICY, **(policy or {})} if legacy else effective_scoring_policy(policy)
    with localcontext() as context:
        context.prec = 100
        base = _score(plan, effective)
        stress = _score(plan.get('stress') or {}, effective) if legacy else None
    score = base['score']
    stress_score = stress['score'] if stress is not None else None
    high, medium = int(effective.get('high_score', 80)), int(effective.get('medium_score', 50))
    priority = ('needs_review' if score is None else 'excluded' if score == 0 else
                'resilient_high' if score >= high and stress_score is not None and stress_score >= high else
                'high' if score >= high else 'consider' if score >= medium else 'low')
    result = {'version': 1 if legacy else 2, 'policy_version': effective['version'], 'policy': effective,
            'score': score, 'priority': priority,
            'feasibility': plan.get('status', 'needs_review'), 'full_economics': plan.get('full_economics'),
            'stock_share': base['S'], 'production_factor': base['P'], 'E': base['E'], 'S': base['S'], 'P': base['P'],
            'utilization': plan.get('utilization'), 'reasons': base['reasons'],
            'data_status': 'confirmed' if score is not None and plan.get('status') == 'feasible' else 'needs_review'}
    if legacy:
        result.update(stress_score=stress_score, stress=stress)
    return result


def opportunity_assessment(card: dict, policy: dict | None = None) -> dict:
    """Rank known economics without changing the historical plan assessment.

    score = round_half_up(100 * clamp((revenue-cost)/revenue/target, 0, 1)
                          * known_share).
    Full plan economics have share 1. Otherwise only confirmed stock economics
    count, weighted by their revenue / the complete order's comparable revenue.
    Missing order prices make that share unknown (and the score zero), never 1.
    Unknown costs are not zero costs: partial economics remain ``confirmed`` and
    reasons/data_status describe the unproved remainder and fulfillment separately.
    """
    effective = effective_scoring_policy(policy)
    plan, analysis, tender = (card.get(key) or {} for key in ('fulfillment', 'analysis', 'tender'))
    reasons = list(plan.get('reasons') or [])
    result = {'version': 1, 'policy_version': effective['version'], 'score': 0,
              'basis': 'unknown', 'known_share': None, 'margin_percent': None,
              'data_status': 'needs_review', 'reasons': reasons}
    full = plan.get('full_economics') or {}
    full_revenue, full_cost = _number(full.get('revenue')), _number(full.get('cost'))
    full_known = (full.get('scope') == 'full' and bool(full.get('currency'))
                  and full_revenue is not None and full_revenue > 0 and full_cost is not None and full_cost >= 0)
    economics = full if full_known else analysis.get('economics') or {}
    revenue, cost = _number(economics.get('revenue')), _number(economics.get('cost'))
    known = (economics.get('scope') in ('full', 'confirmed') and bool(economics.get('currency'))
             and revenue is not None and revenue > 0 and cost is not None and cost >= 0)
    if plan.get('status') == 'infeasible':
        reasons.append('FULFILLMENT_INFEASIBLE')
    if not known:
        reasons.append('ECONOMICS_UNKNOWN')
        result['reasons'] = list(dict.fromkeys(reasons))
        return result
    with localcontext() as context:
        context.prec = 100
        margin = (revenue - cost) / revenue
        result.update(basis='full' if full_known else 'confirmed', margin_percent=_text(100 * margin))
        share = Decimal(1) if full_known else None
        if not full_known:
            reasons.append('FULL_COSTS_UNKNOWN')
            positions = tender.get('positions') or []
            order_revenue = Decimal(0)
            comparable = bool(positions) and not tender.get('limitations')
            for position in positions:
                amount, reason = _price(position, _number(position.get('quantity')))
                if (reason or amount is None or amount < 0
                        or position.get('currency') != economics['currency']):
                    comparable = False
                    break
                order_revenue += amount
            if comparable and order_revenue > 0 and revenue <= order_revenue:
                share = revenue / order_revenue
            else:
                reasons.append('ORDER_REVENUE_UNKNOWN')
        result['known_share'] = _text(share)
        target = _number(effective.get('target_margin'))
        if margin <= 0:
            reasons.append('PLAN_NOT_PROFITABLE' if full_known else 'CONFIRMED_PART_NOT_PROFITABLE')
        elif target is None or target <= 0:
            reasons.append('SCORING_INPUT_UNKNOWN')
        elif share is not None and plan.get('status') != 'infeasible':
            value = 100 * min(Decimal(1), margin / target) * share
            result['score'] = int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
            if value > 0 and result['score'] == 0:
                reasons.append('KNOWN_SHARE_ROUNDS_TO_ZERO')
        if full_known and plan.get('status') == 'feasible':
            result['data_status'] = 'confirmed'
    result['reasons'] = list(dict.fromkeys(reasons))
    return result
