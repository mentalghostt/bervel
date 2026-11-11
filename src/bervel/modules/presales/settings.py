"""Explicit commercial settings; company facts remain owned by company_settings."""
from decimal import Decimal, InvalidOperation

from bervel.core.errors import CoreError
from bervel.modules.presales.models import PresalesSettings


INITIAL_SETTINGS: PresalesSettings = dict(target_margin_percent='50', special_min_margin_percent='70',
                                        seller_contacts='', offer_terms='')


def validate_settings(value: dict) -> PresalesSettings:
    if not isinstance(value, dict) or set(value) != set(INITIAL_SETTINGS):
        raise CoreError('VALIDATION_ERROR', 'Заполните оба процента маржи и текстовые поля настроек пресейла.')
    result = dict(value)
    for field in ('target_margin_percent', 'special_min_margin_percent'):
        raw = value[field]
        try:
            if not isinstance(raw, str) or len(raw) > 40:
                raise ValueError
            number = Decimal(raw)
            if not number.is_finite() or number < 0 or number >= 100 or abs(number.as_tuple().exponent) > 12:
                raise ValueError
        except (InvalidOperation, ValueError):
            raise CoreError('VALIDATION_ERROR', 'Маржа должна быть числом от 0 до 100%, не включая 100%.') from None
        result[field] = format(number.normalize(), 'f')
    if Decimal(result['special_min_margin_percent']) <= Decimal(result['target_margin_percent']):
        raise CoreError('VALIDATION_ERROR', 'Минимальная маржа спецзаказа должна быть выше обычной маржи.')
    for field in ('seller_contacts', 'offer_terms'):
        if not isinstance(value[field], str):
            raise CoreError('VALIDATION_ERROR', 'Контакты и условия должны быть текстом.')
    return result
