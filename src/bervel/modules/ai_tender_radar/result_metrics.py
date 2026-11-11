from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from bervel.modules.ai_tender_radar.calculation import coverage_scale, weighted_piece_coverage
from bervel.modules.ai_tender_radar.models import StockRow


def lot_matches(tender: dict, matches: Callable[[dict], bool | None]) -> dict[int, bool | None]:
    lots = defaultdict(list)
    source = tender.get('source_card') or {}
    lot_count = len(source.get('Lots') or []) or tender.get('lot_count', 0)
    for ordinal in range(1, lot_count + 1):
        lots[ordinal] = []
    for position in tender.get('positions') or []:
        lots[position['lot']].append(matches(position))
    for issue in tender.get('limitations') or []:
        if issue['code'] in {'LOT_OBJECTS_MISSING', 'LOT_OBJECTS_CONFLICT'}:
            lots[issue['lot']].append(None)
    return {lot: False if False in values else True if values and all(values) else None
            for lot, values in lots.items()}


def catalog_percent(lots: dict[int, bool | None]) -> str | None:
    if not lots or None in lots.values():
        return None
    return format(Decimal(100) * sum(outcome is True for outcome in lots.values()) / len(lots), 'f')


def catalog_match_percent(tender: dict, matches: Callable[[dict], bool | None]) -> str | None:
    return catalog_percent(lot_matches(tender, matches))


def saved_stock_coverage(card: dict, tender: dict, catalog: str | None, lines: dict,
                         historical_stock_rows: Callable[[], dict[str, StockRow]] | None) -> dict | None:
    if card.get('state') not in {'accepted', 'needs_data', 'irrelevant', 'failed'}:
        return None
    if 'stock_coverage_by_piece' in card:
        return card['stock_coverage_by_piece']
    lots = lot_matches(tender, lambda p: bool(lines.get(p['id'], {}).get('matches'))
                       or lines.get(p['id'], {}).get('stock_status') == 'full')
    eligible = {lot for lot, matched in lots.items() if matched}
    matching = len(eligible) if catalog is None else int(
        (Decimal(catalog) * len(lots) / 100).to_integral_value(rounding=ROUND_HALF_UP))
    # Historical cards kept the proportion but not the IDs of catalog-matching lots.
    if matching == len(lots):
        eligible = set(lots)
    elif not matching:
        eligible = set()

    quantities = []
    for position in tender.get('positions') or []:
        if position['lot'] not in eligible:
            continue
        line = lines.get(position['id'], {})
        unit = position.get('unit') or line.get('unit')
        scale = coverage_scale(unit, ())
        mass = (line.get('mass_conversion') or {}).get('weight_per_piece_kg')
        matches = line.get('matches') or []
        if scale is None and mass is not None:
            scale = Decimal(1), Decimal(mass)
        elif scale is None and matches and historical_stock_rows is not None:
            rows = historical_stock_rows()
            if all(match['sku'] in rows for match in matches):
                scale = coverage_scale(unit, (rows[match['sku']] for match in matches))
        quantities.append((position.get('quantity') or line.get('required'), line.get('covered'), scale))

    coverage = weighted_piece_coverage(quantities)
    coverage['incomplete'] |= matching != len(eligible) or (catalog != '0' and not lines)
    return coverage


def _price(value) -> str | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0 or abs(number.adjusted()) > 24 or abs(number.as_tuple().exponent) > 24:
        return None
    return format(number, 'f')


def _stage(source: dict, brief: dict, deadline, now: datetime, purchase_stages: list[str] | None) -> str:
    if source.get('CancelReason') or brief.get('IsCancelled') is True:
        return 'cancelled'
    if brief.get('IsAbandoned') is True:
        return 'abandoned'
    if brief.get('IsPlanning') is True:
        return 'planned'
    if purchase_stages and len(purchase_stages) == 1 and purchase_stages[0] in {
            'commission', 'finished', 'planned', 'cancelled'}:
        return purchase_stages[0]
    if isinstance(deadline, str):
        try:
            cutoff = datetime.fromisoformat(deadline)
            if cutoff.utcoffset() is not None:
                return 'accepting' if cutoff > now else 'closed'
        except ValueError:
            pass
    return 'unknown'


def result_metrics(card: dict, *, now: datetime | None = None,
                   historical_stock_rows: Callable[[], dict[str, StockRow]] | None = None) -> dict:
    tender, brief = card.get('tender') or {}, card.get('brief') or {}
    source = tender.get('source_card') or {}
    initial = source.get('InitialSum') or tender.get('initial_sum') or {}
    price = _price(initial.get('Price'))
    currency = initial.get('Currency')
    if price is None:
        price, currency = _price(brief.get('MaxPrice')), brief.get('Currency')
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
        currency = None
    catalog_percent = card.get('catalog_match_percent')
    lines = {line['id']: line for line in (card.get('analysis') or {}).get('positions') or []}
    if 'catalog_match_percent' not in card and lines:
        def saved_match(position):
            line = lines.get(position['id'], {})
            if line.get('matches'):
                return True
            return False if line.get('stock_status') == 'none' else None

        catalog_percent = catalog_match_percent(tender, saved_match)
    # The stored catalog percentage describes whole lots; the product column
    # describes proved technical matches among disclosed rows, regardless of stock.
    positions = tender.get('positions') or list(lines.values())
    confirmed = sum(bool(lines.get(position['id'], {}).get('matches')) for position in positions)
    unknown = sum(not lines.get(position['id'], {}).get('matches') and not (
        lines.get(position['id'], {}).get('stock_status') == 'none'
        and lines.get(position['id'], {}).get('stock_reason') == 'NO_COMPATIBLE_SKU') for position in positions)
    total = len(positions)
    product_percent = format(Decimal(100) * confirmed / total, 'f') if lines and total else None
    product_incomplete = (not lines or bool(unknown) or bool(tender.get('limitations'))
        or (card.get('completeness') or {}).get('requirements') in {'unknown', 'incomplete'}
        or None in lot_matches(tender, lambda _: True).values())
    deadline = source.get('ApplicationDeadline') or tender.get('application_deadline') or brief.get('ApplicationDeadline')
    stage_brief = dict(tender.get('source_stage_flags') or {}) | brief
    coverage = saved_stock_coverage(card, tender, catalog_percent, lines, historical_stock_rows)
    return {'price': price, 'currency': currency.upper() if currency and price is not None else None,
            'stage': _stage(source, stage_brief, deadline, now or datetime.now(timezone.utc), card.get('purchase_stages')),
            'catalog_match_percent': product_percent,
            'catalog_match_confirmed': confirmed, 'catalog_match_unknown': unknown,
            'catalog_match_total': total, 'catalog_match_incomplete': product_incomplete,
            'stock_coverage_percent': coverage['percent'] if coverage else None,
            'stock_coverage_incomplete': coverage['incomplete'] if coverage else False}
