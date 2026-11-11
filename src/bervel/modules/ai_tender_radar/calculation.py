from collections import defaultdict, deque
from collections.abc import Iterable
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from bervel.modules.ai_tender_radar.matching import MatchingResult, restore_matching
from bervel.modules.ai_tender_radar.models import AnalysisResult, BenefitScope, StockData, StockRow, TenderData, TenderPosition


ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
from bervel.product_analysis import (
    CURRENCY_PLACES, normalize_unit as _unit, amount_decimal as _amount,
    decimal_text as _text, quantity_scale as _scale, piece_quantity as _pieces,
    group_stock_allocations as _group_allocations, coverage_scale,
    coverage_for_rows as weighted_piece_coverage,
)

def _economics(revenue: Decimal | None, cost: Decimal | None, currency: str | None, reason: str | None,
               scope: BenefitScope | None = None) -> dict:
    profit = revenue - cost if revenue is not None and cost is not None else None
    margin = HUNDRED * profit / revenue if profit is not None and revenue > ZERO else None
    status = 'unknown' if margin is None else 'profitable' if profit > ZERO else 'loss' if profit < ZERO else 'break_even'
    return {'revenue': _text(revenue), 'cost': _text(cost), 'currency': currency, 'profit': _text(profit),
            'margin_percent': _text(margin), 'status': status, 'scope': scope,
            'reason': (reason or 'REVENUE_NOT_POSITIVE') if margin is None else None}


def _price(pos: TenderPosition, quantity: Decimal | None) -> tuple[Decimal | None, str | None]:
    if not pos['currency']:
        return None, 'TENDER_CURRENCY_UNKNOWN'
    total = _amount(pos['position_price'])
    unit_price = _amount(pos['item_price'])
    derived = unit_price * quantity if unit_price is not None and quantity is not None else None
    if total is not None and derived is not None and total != derived:
        places = CURRENCY_PLACES.get(pos['currency'])
        if places is None:
            return None, 'TENDER_PRICES_CONFLICT'
        minor = ONE.scaleb(-places)
        if total.quantize(minor, rounding=ROUND_HALF_UP) != derived.quantize(minor, rounding=ROUND_HALF_UP):
            return None, 'TENDER_PRICES_CONFLICT'
    result = total if total is not None else derived
    return result, 'TENDER_PRICE_UNKNOWN' if result is None else None


def _position_economics(pos: TenderPosition, quantity: Decimal | None, complete: bool,
                        allocation: dict[str, tuple[Decimal, Decimal]], rows: dict[str, StockRow]) -> dict:
    currency = pos['currency']
    revenue, reason = _price(pos, quantity)
    if not allocation or quantity is None or quantity <= ZERO:
        return _economics(None, None, currency, reason or 'SHORTAGE_COST_UNKNOWN')
    priced_quantity = cost = ZERO
    for sku, (position_amount, stock_amount) in allocation.items():
        row = rows[sku]
        unit_cost = _amount(row['unit_cost'])
        if currency is not None and row['currency'] == currency and unit_cost is not None:
            priced_quantity += position_amount
            cost += stock_amount * unit_cost
    if priced_quantity <= ZERO:
        return _economics(None, None, currency, reason or 'STOCK_COST_OR_CURRENCY_UNKNOWN')
    if revenue is None:
        return _economics(None, cost, currency, reason)
    if complete and all(rows[sku]['unit_cost'] is not None and rows[sku]['currency'] == currency for sku in allocation):
        return _economics(revenue, cost, currency, None, 'full')
    return _economics(revenue * priced_quantity / quantity, cost, currency, None, 'confirmed')


def calculate_analysis(tender: TenderData, stock: StockData, matching: MatchingResult) -> AnalysisResult:
    with localcontext() as context:
        context.prec = 100
        return _calculate(tender, stock, matching)


def refresh_saved_analysis(tender: dict, stock: dict, analysis: dict) -> dict:
    """Project saved proof on its historical stock snapshot, without model calls."""
    saved = deepcopy(analysis)
    matching = restore_matching(tender, stock, saved)
    refreshed = {**saved, **calculate_analysis(tender, stock, matching)}
    for field in ('model', 'model_error', 'mock'):
        if field in saved:
            refreshed[field] = saved[field]
    return refreshed


def stock_coverage_for_lots(tender: TenderData, stock: StockData, matching: MatchingResult,
                            analysis: AnalysisResult, matched_lots: set[int]) -> dict:
    positions = [position for position in tender['positions'] if position['lot'] in matched_lots]
    if not positions:
        return weighted_piece_coverage(())
    rows = {row['sku']: row for row in stock['rows']}
    with localcontext() as context:
        context.prec = 100
        if len(positions) == len(tender['positions']):
            covered = {line['id']: line['covered'] for line in analysis['positions']}
        else:
            _, covered, _, _, _ = _group_allocations(positions, rows, matching)
        return weighted_piece_coverage((
            position['quantity'], covered.get(position['id'], ZERO),
            coverage_scale(position['unit'], (rows[sku] for sku in matching.positions[position['id']].by_sku)),
        ) for position in positions)


def _calculate(tender: TenderData, stock: StockData, matching: MatchingResult) -> AnalysisResult:
    rows = {row['sku']: row for row in stock['rows']}
    allocations, filled, filled_as_pieces, scales, unit_unknown = _group_allocations(tender['positions'], rows, matching)
    lines = []
    needed_pieces = covered_pieces = ZERO
    piece_unknown = False
    any_uncertain = False
    conflicting_lots = {issue.get('lot') for issue in tender['limitations'] if issue['code'] == 'LOT_OBJECTS_CONFLICT'}
    for pos in tender['positions']:
        pos_id = pos['id']
        match = matching.positions[pos_id]
        allocation = allocations.get(pos_id, {})
        quantity = _amount(pos['quantity'])
        covered = filled.get(pos_id, ZERO)
        complete = quantity is not None and quantity > ZERO and covered == quantity
        uncertain = match.uncertain or pos_id in unit_unknown or quantity is None
        any_uncertain |= uncertain
        available = 'full' if complete else 'unconfirmed' if uncertain else 'partial' if covered else 'none'
        scale = scales[pos_id]
        mass_conversion = None
        if _unit(pos['unit']) == 'кг' and scale is not None and quantity is not None:
            position_pieces, filled_pieces = quantity / scale[1], covered / scale[1]
            mass_conversion = {'weight_per_piece_kg': _text(scale[1]), 'required_pieces': _text(position_pieces)}
        else:
            position_pieces = _pieces(quantity, scale)
            filled_pieces = filled_as_pieces[pos_id] if pos_id in filled_as_pieces else _pieces(covered, scale)
        if position_pieces is None or filled_pieces is None:
            piece_unknown = True
        else:
            needed_pieces += position_pieces
            covered_pieces += filled_pieces
        economics = (_economics(None, None, pos['currency'], 'TENDER_SOURCE_INCOMPLETE') if pos['lot'] in conflicting_lots
                     else _position_economics(pos, quantity, complete, allocation, rows))
        verified = [{'sku': sku, 'stock_row': rows[sku]['row'], 'stock_unit': rows[sku]['unit'],
                     'stock_free_quantity': rows[sku]['free_quantity'], 'evidence': match.by_sku[sku]}
                    for sku in sorted(match.by_sku)]
        selected = [{'sku': sku, 'stock_quantity': _text(amount[1]), 'stock_unit': rows[sku]['unit'],
                     'stock_unit_cost': rows[sku]['unit_cost'], 'stock_currency': rows[sku]['currency'],
                     'position_quantity': _text(amount[0])} for sku, amount in allocation.items()]
        stock_reason = {
            'unconfirmed': match.reason or 'STOCK_DATA_NOT_CONFIRMED',
            'none': 'NO_STOCK' if match.by_sku else 'NO_COMPATIBLE_SKU',
            'partial': 'INSUFFICIENT_STOCK',
        }.get(available)
        lines.append({'id': pos_id, 'lot': pos['lot'], 'customer': pos['customer'], 'name': pos['name'], 'unit': pos['unit'],
                      'required': _text(quantity), 'covered': _text(covered),
                      'shortage': _text(quantity - covered) if quantity is not None and (not uncertain or complete) else None,
                      'stock_status': available, 'stock_reason': stock_reason,
                      'matches': verified, 'allocations': selected, 'economics': economics,
                      **({'mass_conversion': mass_conversion} if mass_conversion is not None else {})})

    source_incomplete = bool(tender['limitations']) or not lines
    coverage_reason = 'TENDER_SOURCE_INCOMPLETE' if source_incomplete else 'PIECE_TOTAL_UNKNOWN' if piece_unknown or needed_pieces == ZERO else (
        'STOCK_DATA_NOT_CONFIRMED' if any_uncertain and covered_pieces < needed_pieces else None)
    coverage = HUNDRED * covered_pieces / needed_pieces if coverage_reason is None else None
    priced = [line['economics'] for line in lines if line['economics']['scope'] is not None]
    currencies = {line['currency'] for line in priced}
    economics = _economics(None, None, None, 'TENDER_SOURCE_INCOMPLETE' if source_incomplete else 'TENDER_ECONOMICS_INCOMPLETE')
    if len(currencies) == 1:
        scope = 'full' if not source_incomplete and len(priced) == len(lines) and all(
            line['scope'] == 'full' for line in priced) else 'confirmed'
        economics = _economics(sum((_amount(line['revenue']) for line in priced), ZERO),
                               sum((_amount(line['cost']) for line in priced), ZERO),
                               next(iter(currencies)), None, scope)
    margin = _amount(economics['margin_percent'])
    benefit = max(-HUNDRED, min(HUNDRED, margin)) if margin is not None else None
    return {'version': 1, 'model': matching.model, 'model_error': matching.error, 'mock': stock['data_kind'] == 'mock',
            'status': 'partial' if matching.error or any(match.reason == 'MODEL_CONTEXT_LIMIT' for match in matching.positions.values())
                      else 'insufficient_data' if coverage is None or margin is None or economics['scope'] != 'full' else 'complete',
            'coverage_percent': _text(coverage), 'coverage_reason': coverage_reason, 'benefit_percent': _text(benefit),
            'benefit_clamped': benefit != margin, 'economics': economics, 'positions': lines}
