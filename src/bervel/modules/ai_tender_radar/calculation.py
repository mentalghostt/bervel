from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from bervel.modules.ai_tender_radar.matching import MatchingResult
from bervel.modules.ai_tender_radar.models import AnalysisResult, BenefitScope, StockData, StockRow, TenderData, TenderPosition


ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
UNIT_ALIASES = {
    'шт': ('шт', 'штука', 'штуки', 'штук', '796', 'pc', 'pcs'),
    'кг': ('кг', 'килограмм', 'килограммы', '166', 'kg'),
    'комплект': ('компл', 'комплект', 'комплекты', '839'),
    'упаковка': ('упак', 'упаковка', 'упаковки', '778'),
}
UNITS = {alias: unit for unit, aliases in UNIT_ALIASES.items() for alias in aliases}
COUNT_FIELDS = {'комплект': 'pieces_per_set', 'упаковка': 'pieces_per_package'}
CURRENCY_PLACES = dict.fromkeys(('RUB', 'EUR', 'USD', 'BYN', 'KZT', 'CNY', 'GBP', 'AED', 'TRY', 'UZS'), 2) | {'JPY': 0}
Scale = tuple[Decimal, Decimal]
IDENTITY: Scale = (ONE, ONE)


def _unit(value: str | None) -> str | None:
    if not value:
        return None
    text = value.casefold().rstrip('.')
    return UNITS.get(text, text)


def _amount(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _scale(unit: str | None, row: StockRow) -> Scale | None:
    if unit == 'шт':
        return IDENTITY
    field = 'weight_per_piece_kg' if unit == 'кг' else COUNT_FIELDS.get(unit)
    if field is None or field not in row['attributes']:
        return None
    try:
        value = Decimal(row['attributes'][field].replace(',', '.'))
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= ZERO or value.adjusted() > 29 or abs(value.as_tuple().exponent) > 30:
        return None
    if unit == 'кг':
        return ONE, value
    return (value, ONE) if value == value.to_integral_value() else None


def _pieces(amount: Decimal | None, scale: Scale | None) -> Decimal | None:
    if amount is None or scale is None:
        return None
    numerator = amount * scale[0]
    return numerator // scale[1] if numerator % scale[1] == ZERO else None


def _native(amount: Decimal, scale: Scale) -> Decimal:
    return amount * scale[1] / scale[0]


def _allocate(demands: dict[str, Decimal], supplies: dict[str, Decimal],
              edges: dict[str, list[str]]) -> dict[str, dict[str, Decimal]]:
    """Augment residual paths so a shared SKU can be reassigned to a constrained line."""
    source, sink = ('source', ''), ('sink', '')
    capacity = defaultdict(dict)

    def connect(start, end, amount):
        capacity[start][end] = amount
        capacity[end][start] = ZERO

    for pos_id, demand in demands.items():
        connect(source, ('position', pos_id), demand)
        for sku in edges[pos_id]:
            connect(('position', pos_id), ('sku', sku), demand)
    for sku, supply in supplies.items():
        connect(('sku', sku), sink, supply)

    while True:
        parents = {source: None}
        queue = deque([source])
        while queue and sink not in parents:
            start = queue.popleft()
            for end, remaining in capacity[start].items():
                if remaining > ZERO and end not in parents:
                    parents[end] = start
                    queue.append(end)
        if sink not in parents:
            break
        path = []
        end = sink
        while end != source:
            start = parents[end]
            path.append((start, end))
            end = start
        amount = min(capacity[start][end] for start, end in path)
        for start, end in path:
            capacity[start][end] -= amount
            capacity[end][start] += amount

    return {pos_id: {sku: amount for sku in skus
                     if (amount := capacity[('sku', sku)][('position', pos_id)]) > ZERO}
            for pos_id, skus in edges.items()}


def _group_allocations(positions, rows, matching):
    units = {pos['id']: _unit(pos['unit']) for pos in positions}
    stock_units = {sku: _unit(row['unit']) for sku, row in rows.items()}
    demand_scales = {}
    edges = {}
    reverse = defaultdict(list)
    unconfirmed = set()
    for pos in positions:
        pos_id = pos['id']
        match = matching.positions[pos_id]
        unit = units[pos_id]
        candidates = sorted(match.by_sku)
        scales = {_scale(unit, rows[sku]) for sku in candidates}
        if unit == 'кг':
            scales.discard(None)
        scale = None
        if unit == 'шт':
            scale = IDENTITY
        elif len(scales) == 1 and (unit == 'кг' or not match.uncertain):
            scale = next(iter(scales))
        demand_scales[pos_id] = scale
        edges[pos_id] = []
        for sku in candidates:
            row = rows[sku]
            if row['free_quantity'] is None or pos['quantity'] is None or unit is None:
                unconfirmed.add(pos_id)
                continue
            if _amount(row['free_quantity']) == ZERO:
                continue
            if unit != stock_units[sku]:
                if scale is None or _scale(stock_units[sku], row) is None or (
                    unit == 'кг' and _scale(unit, row) != scale
                ):
                    unconfirmed.add(pos_id)
                    continue
            edges[pos_id].append(sku)
            reverse[sku].append(pos_id)

    positions_by_id = {pos['id']: pos for pos in positions}
    results = {}
    covered = {}
    covered_in_pieces = {}
    visited = set()
    for pos_id in edges:
        if pos_id in visited or not edges[pos_id]:
            continue
        queue = deque([pos_id])
        group = {}
        skus = {}
        visited.add(pos_id)
        while queue:
            current = queue.popleft()
            group[current] = edges[current]
            for sku in edges[current]:
                if sku in skus:
                    continue
                skus[sku] = rows[sku]
                for neighbor in reverse[sku]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
        group_units = {units[key] for key in group} | {stock_units[sku] for sku in skus}
        native = len(group_units) == 1
        pos_scales = {key: IDENTITY if native else demand_scales[key] for key in group}
        sku_scales = {sku: IDENTITY if native else _scale(stock_units[sku], row) for sku, row in skus.items()}
        quantities = {key: _amount(positions_by_id[key]['quantity']) for key in group}
        demands = quantities
        supplies = {sku: _amount(row['free_quantity']) for sku, row in skus.items()}
        if not native:
            demands = {}
            for key, amount in quantities.items():
                scale = pos_scales[key]
                if units[key] == 'кг' and scale is not None:
                    whole, remainder = divmod(amount, scale[1])
                    demands[key] = whole + (ONE if remainder else ZERO)
                else:
                    demands[key] = _pieces(amount, scale)
            supplies = {sku: _pieces(amount, sku_scales[sku]) for sku, amount in supplies.items()}
            if None in demands.values() or None in supplies.values():
                unconfirmed.update(group)
                continue
        allocation = _allocate(demands, supplies, group)
        for key, allocations in allocation.items():
            total = sum(allocations.values(), ZERO)
            mass_converted = not native and units[key] == 'кг'
            covered[key] = _native(total, pos_scales[key])
            if mass_converted:
                covered[key] = min(quantities[key], covered[key])
            if not native and not mass_converted:
                covered_in_pieces[key] = total
            remaining = covered[key]
            results[key] = {}
            for sku, amount in allocations.items():
                position_amount = _native(amount, pos_scales[key])
                if mass_converted:
                    # The final whole piece can weigh more than the outstanding demand.
                    position_amount = min(position_amount, remaining)
                    remaining -= position_amount
                results[key][sku] = position_amount, _native(amount, sku_scales[sku])
    return results, covered, covered_in_pieces, demand_scales, unconfirmed


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
