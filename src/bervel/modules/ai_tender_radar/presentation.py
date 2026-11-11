"""Read-only projection of saved decisions on their historical profile and stock."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext

from bervel.modules.ai_tender_radar.calculation import _price, _scale, _unit, refresh_saved_analysis
from bervel.modules.ai_tender_radar.production import PRODUCTION_RULE_VERSION, evaluate_fulfillment
from bervel.modules.ai_tender_radar.scoring import _number, _text, opportunity_assessment


def present_refresh_warnings(card: dict, checkpoint: dict) -> dict:
    """Only the current logical refresh contributes public per-card warnings."""
    projected = dict(card)
    projected.pop('refresh_warnings', None)
    errors = checkpoint.get('completion_errors') or {}
    warnings = []
    for stage in ('primary', 'detail', 'analysis', 'rating', 'metrics'):
        error = errors.get(stage, {}).get(card['candidate_id'])
        if error:
            warnings.append({'stage': stage, 'code': error['code'], 'message': error['message']})
    if warnings:
        projected['refresh_warnings'] = warnings
    return projected


def _unit_prices(position: dict, line: dict, rows: dict) -> tuple[dict, list[str]]:
    """Use allocated costs, or an unambiguous verified catalog cost at zero stock."""
    unit = _unit(position.get('unit'))
    currency = position.get('currency')
    quantity = _number(position.get('quantity'))
    total, reason = _price(position, quantity)
    tender_price = total / quantity if total is not None and quantity is not None and quantity > 0 else None
    if quantity is None and currency:
        tender_price = _number(position.get('item_price'))
    reasons = [reason] if reason and tender_price is None else []
    if unit is None:
        reasons.append('PRICE_UNIT_UNKNOWN')
        tender_price = None
    verified = {match.get('sku') for match in line.get('matches', []) if match.get('evidence')}
    allocations = [allocation for allocation in line.get('allocations', [])
                   if (_number(allocation.get('position_quantity')) or Decimal(0)) > 0]
    skus = {allocation.get('sku') for allocation in allocations} if allocations else verified
    candidates = [rows[sku] for sku in sorted(skus) if sku in verified and sku in rows]
    currencies = {row.get('currency') for row in candidates}
    if not currency and len(currencies) == 1 and None not in currencies:
        currency = next(iter(currencies))
    stock_cost = None
    if len(candidates) != len(skus) or not candidates:
        reasons.append('STOCK_PRICE_UNKNOWN')
    elif None in currencies or '' in currencies:
        reasons.append('STOCK_CURRENCY_UNKNOWN')
    elif len(currencies) != 1 or next(iter(currencies)) != currency:
        reasons.append('STOCK_CURRENCY_MISMATCH')
    elif any(_number(row.get('unit_cost')) is None or _number(row.get('unit_cost')) < 0 for row in candidates):
        reasons.append('STOCK_PRICE_UNKNOWN')
    elif allocations:
        cost = covered = Decimal(0)
        for allocation in allocations:
            amount = _number(allocation.get('stock_quantity'))
            if amount is None or amount < 0:
                reasons.append('STOCK_PRICE_UNKNOWN')
                break
            cost += amount * _number(rows[allocation['sku']]['unit_cost'])
            covered += _number(allocation['position_quantity'])
        else:
            stock_cost = cost / covered if covered > 0 and unit else None
    elif unit:
        prices = set()
        for row in candidates:
            cost = _number(row['unit_cost'])
            stock_unit = _unit(row.get('unit'))
            if stock_unit == unit:
                prices.add(cost)
                continue
            position_scale, stock_scale = _scale(unit, row), _scale(stock_unit, row)
            if position_scale is None or stock_scale is None:
                reasons.append('PRICE_UNIT_CONVERSION_UNKNOWN')
                break
            prices.add(cost * position_scale[0] * stock_scale[1] / (position_scale[1] * stock_scale[0]))
        else:
            if len(prices) == 1:
                stock_cost = next(iter(prices))
            else:
                reasons.append('STOCK_PRICES_CONFLICT')
    if (line.get('economics') or {}).get('reason') == 'TENDER_SOURCE_INCOMPLETE':
        tender_price = None
        reasons.append('TENDER_SOURCE_INCOMPLETE')
    return {'stock_cost': _text(stock_cost), 'tender_price': _text(tender_price),
            'currency': currency, 'unit': unit}, list(dict.fromkeys(reasons))


def with_unit_prices(tender: dict, stock: dict, analysis: dict) -> dict:
    """Annotate a copy of a verified analysis, including legacy job responses."""
    projected = deepcopy(analysis)
    positions = {position['id']: position for position in tender.get('positions', [])}
    rows = {row['sku']: row for row in stock.get('rows', [])}
    with localcontext() as context:
        context.prec = 100
        for line in projected.get('positions', []):
            position = positions.get(line['id'])
            if position:
                line['unit_prices'], line['unit_price_reasons'] = _unit_prices(position, line, rows)
    return projected


def _report_number(value, *, places: int | None = None) -> str:
    number = _number(value)
    if number is None:
        return '?'
    if places is not None:
        number = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if number == 0:
        number = abs(number)
    text = format(number, ',f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text.replace(',', ' ').replace('.', ',')


def _report_date(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).strftime('%d.%m.%Y')
    except ValueError:
        return None


def _rank_shortages(lines: list[dict]) -> list[dict]:
    shortages = [line for line in lines if (_number(line.get('shortage')) or Decimal(0)) > 0]
    prices = {line['id']: line.get('unit_prices') or {} for line in shortages}
    currencies = {price.get('currency') for price in prices.values()}
    if len(currencies) == 1 and None not in currencies and all(
            _number(price.get('tender_price')) is not None for price in prices.values()):
        return sorted(shortages, key=lambda line: _number(line['shortage']) *
                      _number(prices[line['id']]['tender_price']), reverse=True)
    units = {_unit(line.get('unit')) for line in shortages}
    if len(units) == 1 and None not in units:
        return sorted(shortages, key=lambda line: _number(line['shortage']), reverse=True)
    # Unconvertible kg and pieces cannot be compared by their numeric amounts.
    return sorted(shortages, key=lambda line: _number(line['shortage']) /
                  (_number(line.get('required')) or _number(line['shortage'])), reverse=True)


def _report_summary(card: dict) -> dict:
    analysis, plan, opportunity = (card.get(key) or {} for key in ('analysis', 'fulfillment', 'opportunity'))
    lines = analysis.get('positions') or []
    suitable = sum(bool(line.get('matches')) for line in lines)
    shortages = _rank_shortages(lines)
    details = '; '.join(f"{line.get('name') or line['id']}: обеспечено {_report_number(line.get('covered'))} из "
                        f"{_report_number(line.get('required'))} {line.get('unit') or ''}, "
                        f"дефицит {_report_number(line['shortage'])}"
                        for line in shortages[:3])
    coverage = f"Подтверждено соответствие складских изделий для {suitable} из {len(lines)} позиций. " if lines else ''
    coverage += (details + (f"; ещё позиций с дефицитом: {len(shortages) - 3}." if len(shortages) > 3 else '.')
                 if shortages else 'Склад полностью закрывает указанный объём.' if lines and all(
                     line.get('stock_status') == 'full' for line in lines) else 'Обеспеченный объём требует уточнения.')
    allocations = plan.get('production_allocations') or []
    names = {line['id']: line.get('name') or line['id'] for line in lines}
    by_id = {line['id']: line for line in lines}
    if allocations:
        manufacturing = 'Расчёт производства: ' + '; '.join(
            f"{names.get(item['position_id'], item['position_id'])} — закрыть дефицит "
            f"{_report_number(item.get('shortage'))} {by_id.get(item['position_id'], {}).get('unit') or ''}, "
            + (f"готовность {_report_date(item.get('ready_at'))}" if _report_date(item.get('ready_at'))
               else 'срок готовности не подтверждён')
            for item in allocations[:3]) + '.'
    elif shortages:
        manufacturing = 'Закрытие дефицита производством пока не подтверждено данными профиля.'
    elif lines and all(line.get('stock_status') == 'full' for line in lines):
        manufacturing = 'Дополнительное производство по подтверждённому складскому объёму не требуется.'
    else:
        manufacturing = 'Потребность в дополнительном производстве зависит от уточнения требуемого и обеспеченного объёма.'
    economics = plan.get('full_economics') if opportunity.get('basis') == 'full' else analysis.get('economics')
    economics = economics or {}
    revenue, cost = _number(economics.get('revenue')), _number(economics.get('cost'))
    if opportunity.get('basis') != 'unknown' and revenue is not None and cost is not None:
        scope = 'Полный расчёт заказа' if opportunity['basis'] == 'full' else 'Подтверждённая часть заказа'
        money = (f"{scope}: выручка {_report_number(revenue, places=2)}, "
                 f"известные затраты {_report_number(cost, places=2)}, "
                 f"прибыль {_report_number(revenue - cost, places=2)} {economics.get('currency') or ''}, "
                 f"маржа {_report_number(opportunity['margin_percent'], places=2)}%.")
        if opportunity['basis'] != 'full':
            share = _number(opportunity.get('known_share'))
            money += (f" Это {_report_number(share * 100, places=2)}% стоимости заказа." if share is not None
                      else 'Доля в полной стоимости заказа неизвестна.')
            money += ' Итоговая прибыль заказа неизвестна: расходы на остальной объём и обязательные затраты не подтверждены.'
    else:
        money = 'Прибыль не рассчитана: сопоставимых данных о выручке и затратах недостаточно.'
    reasons = {reason.partition(':')[0] for reason in opportunity.get('reasons', [])}
    gaps = []
    for codes, label in [
        ({'LOGISTICS_COST_UNKNOWN', 'OBLIGATORY_COSTS_UNKNOWN'}, 'стоимость логистики'),
        ({'OTHER_COST_UNKNOWN', 'OBLIGATORY_COSTS_UNKNOWN'}, 'прочие обязательные расходы'),
        ({'TAX_BASIS_UNCONFIRMED'}, 'сопоставимость налоговой базы цен'),
        ({'PRODUCTION_UNCONFIRMED', 'PRODUCTION_FACTS_UNKNOWN', 'PRODUCTION_COST_UNKNOWN'}, 'маршрут и стоимость изготовления дефицита'),
        ({'DELIVERY_DEADLINE_UNKNOWN', 'DELIVERY_START_UNKNOWN', 'DELIVERY_DURATION_UNKNOWN', 'DELIVERY_TERMS_UNKNOWN'}, 'сроки и условия поставки'),
        ({'PRODUCTION_RATE_UNKNOWN', 'MATERIAL_READY_UNKNOWN', 'CAPACITY_UNKNOWN'}, 'доступность материала и производственной мощности'),
        ({'ORDER_REVENUE_UNKNOWN'}, 'полная стоимость заказа'),
        ({'EVALUATION_TIME_UNKNOWN'}, 'дата исходного расчёта для проверки производственного плана'),
    ]:
        if reasons & codes:
            gaps.append(label)
    full_loss = opportunity.get('basis') == 'full' and revenue is not None and cost is not None and revenue <= cost
    decision = ('decline' if plan.get('status') == 'infeasible' or full_loss else
                'participate' if plan.get('status') == 'feasible' and opportunity.get('basis') == 'full'
                and opportunity.get('score', 0) > 0 else 'review')
    conclusion = ('Полный план выполним и имеет положительную расчётную прибыль.' if decision == 'participate' else
                  'Решение об участии требует уточнений по выполнимости плана.' if opportunity.get('basis') == 'full' else
                  'Решение об участии требует уточнений.')
    if decision == 'decline':
        blockers = {
            'CAPACITY_INSUFFICIENT': 'недостаточно производственной мощности для изготовления к сроку',
            'DELIVERY_DEADLINE_PASSED': 'срок поставки уже истёк на дату расчёта',
            'PRODUCTION_RATE_ZERO': 'подтверждённая производительность равна нулю',
            'NEVER_MANUFACTURED': 'профиль подтверждает, что изделие ранее не изготавливалось',
        }
        blocker = None
        for reason in plan.get('reasons') or []:
            code, _, position_id = reason.partition(':')
            if code in blockers:
                blocker = blockers[code] + (f" — {names[position_id]}" if position_id in names else '')
                break
        if blocker:
            conclusion = f'В текущем плане участие не обосновано: {blocker}.'
        elif full_loss:
            comparison = 'равны выручке' if cost == revenue else 'превышают выручку'
            conclusion = f'Участие не даёт расчётной прибыли: полные затраты {comparison}.'
        else:
            conclusion = 'Текущий план подтверждает невозможность выполнить весь заказ.'
    if gaps:
        conclusion += ' Решающие пробелы: ' + ', '.join(gaps[:3]) + '.'
    return {'decision': decision, 'paragraphs': [coverage, manufacturing, money, conclusion]}


def present_unavailable(card: dict, reason: str = 'ORIGINAL_STOCK_UNAVAILABLE') -> dict:
    """Expose saved history without certifying a view that could not be refreshed.

    The caller handles only expected missing/transient dependencies. Access and
    snapshot-integrity errors must propagate instead of entering this path.
    """
    projected = deepcopy(card)
    projected['opportunity'] = dict(opportunity_assessment({}), reasons=[reason])
    projected['projection'] = {'version': 1, 'review_stale': True, 'reasons': [reason]}
    projected['report_summary'] = {
        'decision': 'review',
        'paragraphs': [
            'Исходный снимок склада временно недоступен. Обновить расчёт не удалось; показаны сохранённые данные карточки.',
            'Актуальная оценка не подтверждена. Повторная проверка станет доступна после восстановления доступа к исходному складу.',
        ],
    }
    return projected


def present_card(card: dict, profile: dict, stock: dict) -> dict:
    """Recompute a view only; never mutate saved assessment or source snapshots."""
    projected = deepcopy(card)
    projection = {'version': 1, 'review_stale': bool(card.get('review')) and
                  (card['review'].get('prompt_version') != 'card-review-v5'), 'reasons': []}
    tender, analysis = projected.get('tender'), projected.get('analysis')
    with localcontext() as context:
        context.prec = 100
        if tender and analysis:
            analysis = with_unit_prices(tender, stock, refresh_saved_analysis(tender, stock, analysis))
            projected['analysis'] = analysis
            stamp = projected.get('evaluated_at')
            try:
                now = datetime.fromisoformat(stamp.replace('Z', '+00:00')) if isinstance(stamp, str) else stamp
            except ValueError:
                now = None
            if isinstance(now, datetime) and now.tzinfo is not None:
                original_version = (card.get('fulfillment') or {}).get('version', PRODUCTION_RULE_VERSION)
                projected['fulfillment'] = evaluate_fulfillment(tender, stock, analysis, profile,
                                                               now=now, rule_version=original_version)
            else:
                projection['reasons'].append('EVALUATION_TIME_UNKNOWN')
                # A refreshed match cannot inherit an unrepeatable old certificate.
                projected['fulfillment'] = {
                    'version': (card.get('fulfillment') or {}).get('version', PRODUCTION_RULE_VERSION),
                    'status': 'needs_review', 'stock_allocations': [], 'production_allocations': [],
                    'schedule': [], 'stock_share': None, 'utilization': None,
                    'full_economics': {'revenue': None, 'cost': None, 'profit': None,
                                       'margin_percent': None, 'currency': None, 'scope': None},
                    'costs': {'stock': None, 'production': None},
                    'reasons': ['EVALUATION_TIME_UNKNOWN'], 'assumptions': [],
                }
        projected['opportunity'] = opportunity_assessment(projected, profile.get('scoring_policy'))
        projected['report_summary'] = _report_summary(projected)
    projected['projection'] = projection
    return projected
