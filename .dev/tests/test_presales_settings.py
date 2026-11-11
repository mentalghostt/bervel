from decimal import Decimal

import pytest

from bervel.core.errors import CoreError


def test_settings_are_explicit_visible_and_do_not_supply_missing_fields():
    from bervel.modules.presales.settings import INITIAL_SETTINGS, validate_settings
    assert INITIAL_SETTINGS == {'target_margin_percent': '50', 'special_min_margin_percent': '70',
                                'seller_contacts': '', 'offer_terms': ''}
    assert validate_settings(INITIAL_SETTINGS) == INITIAL_SETTINGS
    with pytest.raises(CoreError):
        validate_settings({'target_margin_percent': '50'})


@pytest.mark.parametrize('ordinary,special', [('100', '101'), ('NaN', '70'), ('50', '50'),
                                             ('-1', '70'), ('50', 'Infinity'), (True, '70')])
def test_invalid_or_ambiguous_margins_are_rejected(ordinary, special):
    from bervel.modules.presales.settings import INITIAL_SETTINGS, validate_settings
    with pytest.raises(CoreError) as caught:
        validate_settings({**INITIAL_SETTINGS, 'target_margin_percent': ordinary,
                           'special_min_margin_percent': special})
    assert caught.value.code == 'VALIDATION_ERROR'


def test_settings_normalize_decimal_strings_and_reject_unknown_keys():
    from bervel.modules.presales.settings import INITIAL_SETTINGS, validate_settings
    result = validate_settings({**INITIAL_SETTINGS, 'target_margin_percent': '50.00'})
    assert Decimal(result['target_margin_percent']) == 50
    with pytest.raises(CoreError):
        validate_settings({**INITIAL_SETTINGS, 'extra': True})
