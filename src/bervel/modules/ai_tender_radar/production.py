"""Finite-capacity, date-based planning; no reservations or external I/O."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, localcontext

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.completeness import check_completeness
from bervel.modules.ai_tender_radar.scoring import LEGACY_DEFAULT_POLICY, _number, _text
from bervel.modules.company_settings.models import production_products
from bervel.product_analysis import production_workdays, schedule_work


ZERO = Decimal(0)
ONE = Decimal(1)
COUNT_UNITS = {'шт', 'шт.', 'штука', '796', 'pc', 'pcs'}
BASELINE_PRODUCTION_RULE_VERSION = 3
PRODUCTION_RULE_VERSION = 4

_SCHEDULING_ONLY = {
    'DELIVERY_SCHEDULE_UNKNOWN', 'DELIVERY_TERMS_UNKNOWN', 'DELIVERY_DEADLINE_PASSED',
    'DELIVERY_DEADLINE_UNKNOWN', 'DELIVERY_START_UNKNOWN', 'DELIVERY_DURATION_UNKNOWN',
    'MATERIAL_READY_UNKNOWN', 'PRODUCTION_RATE_UNKNOWN', 'CAPACITY_UNKNOWN',
    'CAPACITY_INSUFFICIENT', 'UTILIZATION_CAPACITY_UNKNOWN',
}


def _date_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return result if result.tzinfo is not None else None


def _first_day(value: datetime) -> date:
    # A partial day is not represented as one full resource day.
    return value.date() + timedelta(days=bool(value.timetz().replace(tzinfo=None) > time()))


def _fresh(stamp, source: str, policy: dict, now: datetime) -> bool:
    limits = policy.get('max_age_by_source') or {}
    limit = _number(limits.get(source, limits.get('capacity') if source == 'resources' else None))
    confirmed = _date_time(stamp)
    return bool(limit is not None and limit >= 0 and confirmed is not None
                and ZERO <= Decimal(str((now - confirmed).total_seconds())) <= limit * 86400)


def _revenue(position: dict) -> Decimal | None:
    total, price, quantity = (_number(position.get(key)) for key in ('position_price', 'item_price', 'quantity'))
    derived = price * quantity if price is not None and quantity is not None else None
    if total is not None and derived is not None and total != derived:
        return None
    amount = total if total is not None else derived
    return amount if amount is not None and amount > 0 else None


def _quantity(shortage: Decimal, position: dict, product: dict) -> Decimal | None:
    unit, product_unit = (str(obj.get('unit') or '').casefold() for obj in (position, product))
    if unit == product_unit or unit in COUNT_UNITS and product_unit in COUNT_UNITS:
        return shortage.to_integral_value(rounding=ROUND_CEILING) if unit in COUNT_UNITS else shortage
    if unit in {'кг', 'kg', '166'} and product_unit in COUNT_UNITS:
        weight = _number((product.get('attributes') or {}).get('weight_per_piece_kg'))
        return (shortage / weight).to_integral_value(rounding=ROUND_CEILING) if weight is not None and weight > 0 else None
    return None


def _capacity(resource: dict, day: date, *, legacy: bool) -> Decimal | None:
    calendar = resource.get('calendar')
    if not isinstance(calendar, list) or any(type(item) is not int or not 0 <= item <= 6 for item in calendar):
        return None
    if day.weekday() not in calendar:
        return ZERO
    daily = (resource.get('free_capacity_by_day') or {}) if legacy else {}
    amount = _number(daily.get(day.isoformat(), resource.get('default_daily_capacity')))
    return amount if amount is not None and ZERO <= amount <= ONE else None


def _days(start: date, end: date):
    for offset in range(max(0, (end - start).days)):
        yield start + timedelta(days=offset)


def _manufactured(product: dict, rule_version: int) -> bool | None:
    # v2 trusts official catalog membership. v3 also admits active experimental routes.
    # Only v1 requires a separate product-level declaration.
    return True if rule_version >= 2 else product.get('manufactured_before') if product.get('evidence') else None


def _stock_reallocation_possible(line: dict, lines: dict, rows: dict, products: dict,
                                 capabilities: dict, policy: dict, now: datetime, rule_version: int) -> bool:
    """A stock-only maximum flow does not optimize the later production plan.

    Detect an unexamined opportunity to move shared stock to this line. This is
    uncertainty about a failed plan, not a certificate that an alternative works.
    """
    wanted = {match['sku'] for match in line.get('matches', []) if match.get('evidence')}
    for other in lines.values():
        if other.get('id') == line.get('id'):
            continue
        shared = {allocation.get('sku') for allocation in other.get('allocations', [])
                  if (_number(allocation.get('stock_quantity')) or ZERO) > ZERO} & wanted
        if not shared:
            continue
        for match in other.get('matches', []):
            sku = match.get('sku')
            if not match.get('evidence'):
                continue
            # The same SKU can be made for the later owner of stock, freeing its
            # allocation for this early line. Its shared stock is already spent.
            stock_row = rows.get(sku) if sku not in shared else None
            if stock_row is not None:
                amount = _number(stock_row.get('free_quantity'))
                if amount is None or amount > ZERO:
                    return True
            for product in products.get(sku, []):
                manufactured = _manufactured(product, rule_version)
                if manufactured is False:
                    continue
                routes = capabilities.get(product['id'], [])
                if not routes or manufactured is None:
                    return True
                if any(_number(route.get('units_per_workday')) != ZERO
                       or (rule_version < BASELINE_PRODUCTION_RULE_VERSION
                           and not _fresh(route.get('confirmed_at'), 'production', policy, now))
                       or not route.get('evidence') or route.get('operations') for route in routes):
                    return True
    return False


def _plan(tender: dict, stock: dict, analysis: dict, profile: dict, now: datetime,
          *, price_factor: Decimal, cost_factor: Decimal, rate_factor: Decimal, rule_version: int,
          submission_cutoff: datetime | None = None, scheduling: bool = True) -> dict:
    legacy = rule_version < BASELINE_PRODUCTION_RULE_VERSION
    separate_economics = rule_version >= PRODUCTION_RULE_VERSION
    reasons, hard = [], []
    completeness = check_completeness(tender)
    if not completeness['complete']:
        reasons.extend(completeness['reasons'] or ['TENDER_SOURCE_INCOMPLETE'])
    assumptions = ['Resource days represent whole calendar workdays; partial days are excluded.',
                   'This is an execution estimate, not a reservation or a globally optimal route.']
    terms = tender.get('terms') or {}
    policy = profile.get('scoring_policy') or {}
    deadline = _date_time(terms.get('delivery_deadline'))
    stated_start = _date_time(terms.get('start_at'))
    delivery_days = terms.get('delivery_days')
    if submission_cutoff is not None:
        deadline, stated_start, delivery_days = submission_cutoff, now, 0
    missing_schedule = [
        reason for missing, reason in (
            (deadline is None, 'DELIVERY_DEADLINE_UNKNOWN'),
            (stated_start is None, 'DELIVERY_START_UNKNOWN'),
            (type(delivery_days) is not int or delivery_days < 0, 'DELIVERY_DURATION_UNKNOWN'),
        ) if missing
    ]
    if not scheduling:
        missing_schedule = []
    if separate_economics:
        reasons.extend(missing_schedule)
    elif missing_schedule:
        reasons.append('DELIVERY_TERMS_UNKNOWN')
    start = max(now, stated_start) if stated_start else now
    if deadline is not None:
        start = start.astimezone(deadline.tzinfo)
    ship_end = deadline - timedelta(days=delivery_days) if deadline and type(delivery_days) is int and delivery_days >= 0 else None
    if scheduling and ship_end is not None and ship_end < start:
        hard.append('DELIVERY_DEADLINE_PASSED')
    logistics, other = _number(terms.get('logistics_cost')), _number(terms.get('other_cost'))
    missing_logistics = logistics is None or logistics < 0
    missing_other = other is None or other < 0
    if separate_economics:
        if missing_logistics:
            reasons.append('LOGISTICS_COST_UNKNOWN')
        if missing_other:
            reasons.append('OTHER_COST_UNKNOWN')
    elif missing_logistics or missing_other:
        reasons.append('OBLIGATORY_COSTS_UNKNOWN')
    if terms.get('tax_comparable') is not True:
        reasons.append('TAX_BASIS_UNCONFIRMED')
    if legacy and not _fresh(terms.get('costs_confirmed_at', terms.get('confirmed_at')), 'costs', policy, now):
        reasons.append('COSTS_FRESHNESS_UNCONFIRMED')
    if tender.get('limitations') or not tender.get('positions'):
        reasons.append('TENDER_SOURCE_INCOMPLETE')
    rows = {row['sku']: row for row in stock.get('rows', [])}
    products = defaultdict(list)
    if legacy:
        active_products = {product['id'] for product in profile.get('products', []) if product.get('enabled')}
        if rule_version == 1 and profile.get('include_experimental'):
            active_products.update(product.get('product_id') for product in profile.get('experimental_products', []) if product.get('enabled'))
        for product in profile.get('products', []):
            if product['id'] in active_products:
                product = dict(product, sku=product.get('sku') or 'CAT:' + product['id'])
                products[product['sku']].append(product)
    else:
        for sku, product in production_products(profile).items():
            products[sku].append(dict(product, sku=sku))
    resources = {resource['group_id']: resource for resource in profile.get('resources', [])
                 if legacy or resource.get('enabled', True)}
    capabilities = defaultdict(list)
    for capability in profile.get('production', []):
        if legacy or capability.get('resource_group_id') in resources:
            capabilities[capability.get('product_id')].append(capability)
    lines = {line['id']: line for line in analysis.get('positions', [])}
    spent_stock = defaultdict(lambda: ZERO)
    revenue = stock_value = stock_cost = production_cost = ZERO
    currency = None
    jobs, production_allocations, stock_allocations = [], [], []
    production_required = 0
    for position in tender.get('positions', []):
        pos_id = position['id']
        line_deadline = submission_cutoff or _date_time(position.get('delivery_deadline')) or deadline
        end = line_deadline - timedelta(days=delivery_days) if line_deadline and type(delivery_days) is int and delivery_days >= 0 else None
        if scheduling and end is not None and end < start:
            hard.append(f'DELIVERY_DEADLINE_PASSED:{pos_id}')
        line = lines.get(pos_id) or {}
        required, covered = _number(line.get('required')), _number(line.get('covered'))
        position_revenue = _revenue(position)
        if position_revenue is None or not position.get('currency'):
            reasons.append(f'REVENUE_UNKNOWN:{pos_id}')
        else:
            revenue += position_revenue * price_factor
        if currency is None:
            currency = position.get('currency')
        if currency != position.get('currency'):
            reasons.append('MIXED_CURRENCIES')
        if required is None or required <= 0 or covered is None or not ZERO <= covered <= required or _number(line.get('shortage')) is None:
            reasons.append(f'QUANTITY_OR_MATCH_UNKNOWN:{pos_id}')
            continue
        if _number(position.get('quantity')) != required:
            reasons.append(f'QUANTITY_CONFLICT:{pos_id}')
            continue
        shortage = max(ZERO, required - covered)
        if _number(line.get('shortage')) != shortage:
            reasons.append(f'SHORTAGE_CONFLICT:{pos_id}')
            continue
        if position_revenue is not None:
            stock_value += position_revenue * price_factor * covered / required
        allocated = ZERO
        for allocation in line.get('allocations', []):
            sku = allocation.get('sku')
            amount, position_amount, unit_cost = (_number(allocation.get(key)) for key in ('stock_quantity', 'position_quantity', 'stock_unit_cost'))
            row = rows.get(sku) or {}
            if amount is None or amount < 0 or position_amount is None or position_amount < 0:
                reasons.append(f'STOCK_ALLOCATION_INVALID:{pos_id}')
                continue
            allocated += position_amount
            spent_stock[sku] += amount
            available = _number(row.get('free_quantity'))
            if available is None or spent_stock[sku] > available:
                reasons.append(f'STOCK_OVERALLOCATED:{sku}')
            if legacy and not _fresh(row.get('as_of'), 'stock', policy, now):
                reasons.append(f'STOCK_FRESHNESS_UNCONFIRMED:{sku}')
            if legacy and not _fresh(row.get('as_of'), 'costs', policy, now):
                reasons.append(f'STOCK_COST_FRESHNESS_UNCONFIRMED:{sku}')
            if unit_cost is None or unit_cost < 0 or allocation.get('stock_currency') != currency:
                reasons.append(f'STOCK_COST_UNKNOWN:{sku}')
            else:
                stock_cost += amount * unit_cost * cost_factor
            stock_allocations.append({'position_id': pos_id, **allocation})
        if allocated != covered:
            reasons.append(f'STOCK_ALLOCATION_CONFLICT:{pos_id}')
        if shortage == 0:
            continue
        production_required += 1
        stock_alternative = _stock_reallocation_possible(line, lines, rows, products, capabilities, policy, now, rule_version)
        candidates = []
        flags = []
        for match in line.get('matches', []):
            if not match.get('evidence'):
                continue
            for product in products[match.get('sku')]:
                flags.append(_manufactured(product, rule_version))
                if _manufactured(product, rule_version) is True:
                    for capability in capabilities[product['id']]:
                        candidates.append((product, capability))
        if not candidates:
            if stock_alternative:
                reasons.append(f'ALTERNATIVE_STOCK_ALLOCATION_UNRESOLVED:{pos_id}')
            else:
                (hard if flags and all(flag is False for flag in flags) else reasons).append(
                    f'NEVER_MANUFACTURED:{pos_id}' if flags and all(flag is False for flag in flags) else f'PRODUCTION_UNCONFIRMED:{pos_id}')
            continue
        product, capability = candidates[0]
        resource = resources.get(capability.get('resource_group_id'))
        rate, unit_cost, setup = (_number(capability.get(key)) for key in ('units_per_workday', 'production_unit_cost', 'setup_workdays'))
        material = _date_time(capability.get('material_ready_at'))
        quantity = _quantity(shortage, position, product)
        if rate == ZERO:
            proven_zero = None not in flags and all(
                _number(candidate.get('units_per_workday')) == ZERO
                and (not legacy or _fresh(candidate.get('confirmed_at'), 'production', policy, now))
                and candidate.get('evidence') and (rule_version >= 2 or candidate_product.get('evidence'))
                and not candidate.get('operations') for candidate_product, candidate in candidates)
            if stock_alternative:
                reasons.append(f'ALTERNATIVE_STOCK_ALLOCATION_UNRESOLVED:{pos_id}')
            (hard if proven_zero and not stock_alternative else reasons).append(
                f'PRODUCTION_RATE_ZERO:{pos_id}' if proven_zero else f'PRODUCTION_RATE_UNCONFIRMED:{pos_id}')
            continue
        cost_known = unit_cost is not None and unit_cost >= 0 and capability.get('currency') == currency
        can_schedule = rate is not None and rate > 0 and material is not None
        if (setup is None or setup < 0 or quantity is None or resource is None
                or (not separate_economics and (not cost_known or not can_schedule))
                or not capability.get('evidence') or (rule_version == 1 and not product.get('evidence')) or capability.get('operations')):
            reasons.append(f'PRODUCTION_FACTS_UNKNOWN:{pos_id}')
            continue
        production_confirmed = not legacy or _fresh(capability.get('confirmed_at'), 'production', policy, now)
        resource_confirmed = not legacy or _fresh(resource.get('confirmed_at'), 'resources', policy, now)
        if not production_confirmed:
            reasons.append(f'PRODUCTION_FRESHNESS_UNCONFIRMED:{pos_id}')
        if legacy and not _fresh(capability.get('confirmed_at'), 'costs', policy, now):
            reasons.append(f'PRODUCTION_COST_FRESHNESS_UNCONFIRMED:{pos_id}')
        if not resource_confirmed:
            reasons.append(f'RESOURCE_FRESHNESS_UNCONFIRMED:{resource["group_id"]}')
        setup_cost = _number(capability.get('setup_cost', '0' if setup == 0 else None))
        if not cost_known:
            reasons.append(f'PRODUCTION_COST_UNKNOWN:{pos_id}')
        elif setup_cost is None or setup_cost < 0:
            reasons.append(f'SETUP_COST_UNKNOWN:{pos_id}')
        else:
            production_cost += (quantity * unit_cost + setup_cost) * cost_factor
        if not separate_economics and (setup_cost is None or setup_cost < 0):
            continue
        if not scheduling:
            # Choosing the first route cannot certify its quoted cost while
            # alternatives or a different shared-stock allocation remain open.
            if len(candidates) > 1 or None in flags or stock_alternative:
                reasons.append(f'ALTERNATIVE_SCHEDULE_UNRESOLVED:{pos_id}')
            continue
        if not can_schedule:
            if rate is None or rate < 0:
                reasons.append(f'PRODUCTION_RATE_UNKNOWN:{pos_id}')
            if material is None:
                reasons.append(f'MATERIAL_READY_UNKNOWN:{pos_id}')
            continue
        if end is None:
            continue
        end = end.astimezone(start.tzinfo)
        work = production_workdays(quantity, rate, setup, rate_factor=rate_factor)
        job = {'position_id': pos_id, 'sku': product['sku'], 'resource_group_id': resource['group_id'],
               'quantity': _text(quantity), 'shortage': _text(shortage), 'workdays': _text(work),
               'release': _first_day(max(start, material).astimezone(end.tzinfo)), 'deadline': end.date(), 'work': work,
               'alternatives': len(candidates) > 1 or None in flags or stock_alternative,
               'stock_reallocation': stock_alternative,
               'confirmed': production_confirmed and resource_confirmed}
        jobs.append(job)
        production_allocations.append({key: value for key, value in job.items() if key not in {'work', 'release', 'deadline', 'alternatives', 'confirmed', 'stock_reallocation'}})

    if any(terms.get(field) is not None and terms[field] != currency
           for field in ('logistics_currency', 'other_currency', 'other_cost_currency')):
        reasons.append('COST_CURRENCY_MISMATCH')
    placement = schedule_work(jobs, resources, capacity=lambda resource, day: _capacity(resource, day, legacy=legacy))
    schedule = [{**{key: value for key, value in block.items() if key != 'work'},
                 'workdays': _text(block['work'])} for block in placement.schedule]
    original_capacity, remaining = placement.original_capacity, placement.remaining
    completed = []
    for job in sorted(jobs, key=lambda item: (item['deadline'], item['position_id'])):
        group = job['resource_group_id']
        work = placement.unplaced.get(job['position_id'], ZERO)
        unknown = job['position_id'] in placement.unknown
        if work == 0:
            completed.append(job)
            allocation = next(item for item in production_allocations if item['position_id'] == job['position_id'])
            allocation['ready_at'] = datetime.combine(placement.ready_at[job['position_id']], time(), start.tzinfo).isoformat()
        if work > 0:
            if job['stock_reallocation']:
                reasons.append(f'ALTERNATIVE_STOCK_ALLOCATION_UNRESOLVED:{job["position_id"]}')
            total_capacity = sum((original_capacity.get((group, day), ZERO) for day in _days(job['release'], job['deadline'])), ZERO)
            # Only routes that could release this resource can invalidate a failed
            # placement. An unrelated alternative cannot rescue an impossible line.
            competing_alternative = job['work'] <= total_capacity and any(
                other['alternatives'] and other['position_id'] != job['position_id'] and other['resource_group_id'] == group
                and any(block['position_id'] == other['position_id']
                        and job['release'] <= date.fromisoformat(block['date']) < job['deadline'] for block in schedule)
                for other in jobs)
            unresolved_alternatives = job['alternatives'] or competing_alternative
            (reasons if unknown or unresolved_alternatives or not job['confirmed'] else hard).append(
                f'CAPACITY_UNKNOWN:{job["position_id"]}' if unknown else
                f'ALTERNATIVE_SCHEDULE_UNRESOLVED:{job["position_id"]}' if unresolved_alternatives else
                f'CAPACITY_FRESHNESS_UNCONFIRMED:{job["position_id"]}' if not job['confirmed'] else
                f'CAPACITY_INSUFFICIENT:{job["position_id"]}')
    # Check the schedule certificate independently of placement state.
    used = defaultdict(lambda: ZERO)
    for item in schedule:
        used[(item['resource_group_id'], item['date'])] += Decimal(item['workdays'])
    if any(amount > (_capacity(resources[group], date.fromisoformat(day), legacy=legacy) or ZERO)
           for (group, day), amount in used.items()):
        reasons.append('SCHEDULE_CERTIFICATE_INVALID')
    for job in completed:
        blocks = [item for item in schedule if item['position_id'] == job['position_id']]
        if (sum((Decimal(item['workdays']) for item in blocks), ZERO) != job['work']
                or any(item['resource_group_id'] != job['resource_group_id']
                       or not job['release'] <= date.fromisoformat(item['date']) < job['deadline'] for item in blocks)):
            reasons.append('SCHEDULE_CERTIFICATE_INVALID')
    utilization = ZERO
    for job in completed:
        prefix = [item for item in completed if item['resource_group_id'] == job['resource_group_id'] and item['deadline'] <= job['deadline']]
        ids = {item['position_id'] for item in prefix}
        blocks = [item for item in schedule if item['position_id'] in ids]
        busy = sum((Decimal(item['workdays']) for item in blocks), ZERO)
        finish_day = max(date.fromisoformat(item['date']) for item in blocks)
        # Include the unused fraction on the finishing workday, but no capacity before material release.
        capacity = _capacity(resources[job['resource_group_id']], finish_day, legacy=legacy)
        last_busy = sum((Decimal(item['workdays']) for item in blocks if item['date'] == finish_day.isoformat()), ZERO)
        free = (capacity or ZERO) - last_busy
        for day in _days(finish_day + timedelta(days=1), job['deadline']):
            available = _capacity(resources[job['resource_group_id']], day, legacy=legacy)
            if available is None:
                reasons.append(f'UTILIZATION_CAPACITY_UNKNOWN:{job["resource_group_id"]}')
            else:
                free += available
        if busy > 0:
            utilization = max(utilization, busy / (busy + free))
    reasons = list(dict.fromkeys(reasons))
    status = 'infeasible' if hard else 'needs_review' if reasons else 'feasible'
    # These reasons only qualify the schedule. Unknown quantities, costs, or
    # possible alternatives still invalidate the quoted plan's full economics.
    complete = (not reasons and status == 'feasible') or (
        separate_economics and all(reason.partition(':')[0] in _SCHEDULING_ONLY for reason in hard + reasons))
    cost = stock_cost + production_cost + (logistics + other) * cost_factor if logistics is not None and other is not None else None
    profit = revenue - cost if complete and cost is not None else None
    economics = {'revenue': _text(revenue) if complete else None, 'cost': _text(cost) if complete else None,
                 'profit': _text(profit), 'margin_percent': _text(100 * profit / revenue) if profit is not None and revenue > 0 else None,
                 'currency': currency, 'scope': 'full' if complete else None}
    utilization_known = len(completed) == len(jobs) and (not separate_economics or len(jobs) == production_required)
    return {'version': rule_version, 'status': status, 'stock_allocations': stock_allocations,
            'production_allocations': production_allocations, 'schedule': schedule,
            'stock_share': _text(stock_value / revenue) if revenue > 0 and not any(reason.startswith(('REVENUE_', 'QUANTITY_', 'TENDER_SOURCE')) for reason in reasons) else None,
            'utilization': _text(utilization) if utilization_known else None,
            'full_economics': economics, 'costs': {'stock': _text(stock_cost), 'production': _text(production_cost)},
            'reasons': list(dict.fromkeys(hard + reasons)), 'assumptions': assumptions}


def evaluate_fulfillment(tender: dict, stock: dict, analysis: dict, profile: dict, *, now: datetime | None = None,
                         rule_version: int = PRODUCTION_RULE_VERSION) -> dict:
    """Evaluate a saved plan with the rules selected when its run was created."""
    now = now or datetime.now(timezone.utc)
    if type(rule_version) is not int or rule_version not in (1, 2, BASELINE_PRODUCTION_RULE_VERSION, PRODUCTION_RULE_VERSION):
        raise CoreError('VALIDATION_ERROR', 'Неизвестная версия производственных правил.')
    if now.tzinfo is None:
        raise ValueError('now must contain a timezone')
    with localcontext() as context:
        context.prec = 100
        plan = _plan(tender, stock, analysis, profile, now, price_factor=ONE, cost_factor=ONE, rate_factor=ONE, rule_version=rule_version)
        if rule_version >= BASELINE_PRODUCTION_RULE_VERSION:
            return plan
        policy = {**LEGACY_DEFAULT_POLICY, **(profile.get('scoring_policy') or {})}
        factors = [_number(policy.get(key)) for key in ('stress_price_factor', 'stress_cost_factor', 'stress_rate_factor')]
        if any(factor is None or factor <= 0 for factor in factors):
            plan['stress'] = {'status': 'needs_review', 'reasons': ['STRESS_POLICY_INVALID'], 'full_economics': {'scope': None}}
        else:
            plan['stress'] = _plan(tender, stock, analysis, profile, now,
                                   price_factor=factors[0], cost_factor=factors[1], rate_factor=factors[2], rule_version=rule_version)
    return plan


def evaluate_economics(tender: dict, stock: dict, analysis: dict, profile: dict, *, now: datetime) -> dict:
    """Use the same costing rules without scheduling resource days or readiness."""
    with localcontext() as context:
        context.prec = 100
        plan = _plan(tender, stock, analysis, profile, now, price_factor=ONE, cost_factor=ONE,
            rate_factor=ONE, rule_version=PRODUCTION_RULE_VERSION, scheduling=False)
    return {'full_economics': plan['full_economics'], 'reasons': plan['reasons'], 'status': 'needs_review'}


def evaluate_submission_readiness(tender: dict, stock: dict, analysis: dict, profile: dict,
                                  *, now: datetime, deadline: datetime | None) -> dict:
    """Reuse finite-capacity scheduling for a separate pre-submission production window."""
    if now.tzinfo is None or deadline is not None and deadline.tzinfo is None:
        raise ValueError('Production dates must contain a timezone')
    # Resource weekday/whole-day calendars are business-local, not dependent on
    # whether the source spelled the same instant with Z or +03:00.
    business_timezone = timezone(timedelta(hours=3))
    now = now.astimezone(business_timezone)
    deadline = deadline.astimezone(business_timezone) if deadline else None
    cutoff = deadline - timedelta(hours=48) if deadline else None
    result = {'status': 'unknown', 'application_deadline': deadline.isoformat() if deadline else None,
        'cutoff': cutoff.isoformat() if cutoff else None, 'ready_at': None,
        'required_positions': 0, 'completed_positions': 0, 'workdays': None, 'reasons': []}
    lines = {line['id']: line for line in analysis.get('positions', [])}
    required = set()
    if check_completeness(tender)['requirements'] != 'complete':
        result['reasons'].append('TENDER_REQUIREMENTS_INCOMPLETE')
    for position in tender.get('positions', []):
        line = lines.get(position['id']) or {}
        quantity, covered, shortage = (_number(line.get(key)) for key in ('required', 'covered', 'shortage'))
        if (quantity is None or quantity <= 0 or covered is None or not ZERO <= covered <= quantity
                or _number(position.get('quantity')) != quantity or shortage != quantity - covered):
            result['reasons'].append('QUANTITY_OR_MATCH_UNKNOWN:' + position['id'])
        elif shortage > 0:
            required.add(position['id'])
    result['required_positions'] = len(required)
    if result['reasons']:
        return result
    if not required:
        result.update(status='not_required', ready_at=now.isoformat(), workdays='0')
        return result
    if cutoff is None:
        result['reasons'].append('APPLICATION_DEADLINE_UNKNOWN')
        return result
    with localcontext() as context:
        context.prec = 100
        plan = _plan(tender, stock, analysis, profile, now, price_factor=ONE, cost_factor=ONE,
                     rate_factor=ONE, rule_version=PRODUCTION_RULE_VERSION, submission_cutoff=cutoff)
    allocations = plan['production_allocations']
    completed = {item['position_id'] for item in allocations if item.get('ready_at')}
    result['completed_positions'] = len(completed)
    result['workdays'] = _text(sum((_number(item['workdays']) for item in allocations), ZERO)) if allocations else None
    # Financial gaps do not invalidate a known schedule. A missing production
    # route/material/rate still leaves its required position uncompleted.
    invalid_stock = [reason for reason in plan['reasons'] if reason.startswith(
        ('STOCK_OVERALLOCATED:', 'STOCK_ALLOCATION_INVALID:', 'STOCK_ALLOCATION_CONFLICT:'))]
    if required <= completed and not invalid_stock:
        result.update(status='on_time', ready_at=max(item['ready_at'] for item in allocations))
    else:
        missing = required - completed
        proven_late = any(reason in {f'CAPACITY_INSUFFICIENT:{key}', f'PRODUCTION_RATE_ZERO:{key}'}
                          for key in missing for reason in plan['reasons'])
        result['status'] = 'late' if proven_late and not invalid_stock else 'unknown'
        result['reasons'] = list(dict.fromkeys(invalid_stock + [reason for reason in plan['reasons']
            if reason.partition(':')[2] in missing or reason.partition(':')[0] in _SCHEDULING_ONLY]))
    return result
