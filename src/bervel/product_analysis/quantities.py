"""Quantity normalization and shared stock allocation; no economics."""
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation, localcontext
from types import SimpleNamespace
from .models import Demand, StockRow, StockData, MatchResult, AllocationResult, CoverageResult

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


def coverage_scale(unit: str | None, candidates: Iterable[StockRow]) -> Scale | None:
    unit = _unit(unit)
    if unit == 'шт':
        return IDENTITY
    scales = {_scale(unit, row) for row in candidates}
    if unit == 'кг':
        scales.discard(None)
    return next(iter(scales)) if len(scales) == 1 else None


def _weighted_piece_coverage(lines: Iterable[tuple[str | None, str | Decimal | None, Scale | None]]) -> dict:
    with localcontext() as context:
        context.prec = 100
        needed = filled = ZERO
        incomplete = False
        for required, covered, scale in lines:
            quantity = _amount(required)
            if quantity is None or scale is None:
                incomplete = True
                continue
            if quantity <= ZERO:
                continue
            if covered is None:
                incomplete = True
            available = Decimal(covered) if covered is not None else ZERO
            numerator, denominator = scale
            needed += quantity * numerator / denominator
            filled += min(quantity, max(ZERO, available)) * numerator / denominator
        return {'percent': _text(HUNDRED * filled / needed) if needed else '0', 'incomplete': incomplete}


def normalize_unit(value: str | None) -> str | None:
    return _unit(value)


def convert_quantity(amount: Decimal, from_unit: str | None, to_unit: str | None, row: StockRow) -> Decimal | None:
    source, target = _unit(from_unit), _unit(to_unit)
    if not source or not target:
        return None
    if source == target:
        return amount
    left, right = _scale(source, row), _scale(target, row)
    if left is None or right is None:
        return None
    return amount * left[0] / left[1] * right[1] / right[0]


def convert_unit_cost(cost: Decimal, from_unit: str | None, to_unit: str | None, row: StockRow) -> Decimal | None:
    factor = convert_quantity(ONE, to_unit, from_unit, row)
    return cost * factor if factor is not None else None


def allocate_stock(demands: Sequence[Demand], stock: StockData, matches: Mapping[str, MatchResult]) -> AllocationResult:
    rows = {row['sku']: row for row in stock['rows']}
    with localcontext() as context:
        context.prec = 100
        allocations, covered, pieces, scales, unknown = _group_allocations(demands, rows, SimpleNamespace(positions=matches))
        for demand in demands:
            key = demand['id']
            if demand['quantity'] is None or matches[key].uncertain:
                unknown.add(key)
            if key not in unknown:
                covered.setdefault(key, ZERO)
        coverage_rows = [(d['quantity'], covered.get(d['id']),
                          coverage_scale(d['unit'], (rows[sku] for sku in matches[d['id']].by_sku)))
                         for d in demands]
        result = _weighted_piece_coverage(coverage_rows)
        known_denominator = any(q is not None and Decimal(q) > 0 and s is not None for q,c,s in coverage_rows)
        coverage = CoverageResult(Decimal(result['percent']) if known_denominator else None, result['incomplete'])
        return AllocationResult(allocations, covered, pieces, scales, unknown, coverage)


def weighted_piece_coverage(allocation: AllocationResult) -> CoverageResult:
    return allocation.coverage


