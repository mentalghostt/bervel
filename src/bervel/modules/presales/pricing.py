"""Presales quotes use warehouse cost and a visible margin, never production cost."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP, localcontext

CURRENCY_QUANTA = {key: Decimal('.01') for key in ('RUB','USD','EUR','BYN','KZT','CNY','GBP','AED','TRY','UZS')}
CURRENCY_QUANTA['JPY'] = Decimal('1')


def decimal_value(value) -> Decimal | None:
    if not isinstance(value, (str, Decimal)):
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 and result.adjusted() <= 29 and abs(result.as_tuple().exponent) <= 30 else None


def number_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    result = format(value, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


def quote_unit_price(unit_cost: Decimal, margin_percent: Decimal, quantum: Decimal = Decimal('.01')) -> Decimal:
    if any(not isinstance(value, Decimal) for value in (unit_cost, margin_percent, quantum)):
        raise TypeError('Money and margin must be Decimal')
    if any(not value.is_finite() for value in (unit_cost, margin_percent, quantum)):
        raise ValueError('Money and margin must be finite')
    if unit_cost < 0 or not 0 <= margin_percent < 100 or quantum <= 0:
        raise ValueError('Cost, margin or monetary precision out of range')
    with localcontext() as context:
        context.prec = 100
        unrounded = unit_cost / (1 - margin_percent / 100)
        return ((unrounded / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum).quantize(quantum)


def line_amount(unit_price: Decimal, quantity: Decimal, currency: str) -> Decimal:
    if currency not in CURRENCY_QUANTA:
        raise ValueError('Unknown monetary precision')
    if any(not isinstance(value,Decimal) for value in (unit_price,quantity)):
        raise TypeError('Price and quantity must be Decimal')
    if any(not value.is_finite() or value < 0 for value in (unit_price,quantity)):
        raise ValueError('Price and quantity must be finite and nonnegative')
    with localcontext() as context:
        context.prec = 100
        return (unit_price*quantity).quantize(CURRENCY_QUANTA[currency],rounding=ROUND_HALF_UP)
