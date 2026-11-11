from decimal import Decimal as D
import pytest

def test_price_uses_margin_and_rounds_up_to_preserve_it():
    from bervel.modules.presales.pricing import quote_unit_price
    assert quote_unit_price(D('100'), D('50'), D('.01')) == D('200.00')
    assert quote_unit_price(D('100'), D('70'), D('.01')) == D('333.34')

@pytest.mark.parametrize('cost,margin', [(1.0,D('50')), (D('-1'),D('50')), (D('1'),D('100')), (D('NaN'),D('50'))])
def test_invalid_money_cannot_be_quoted(cost, margin):
    from bervel.modules.presales.pricing import quote_unit_price
    with pytest.raises((ValueError,TypeError)):
        quote_unit_price(cost, margin)

def test_line_total_rounds_in_its_currency_from_the_displayed_unit_price():
    from bervel.modules.presales.pricing import line_amount
    assert line_amount(D('333'),D('1.5'),'JPY')==D('500')
    assert line_amount(D('33.33'),D('1.5'),'RUB')==D('50.00')
    with pytest.raises(ValueError):
        line_amount(D('1'),D('1'),'UNKNOWN')
