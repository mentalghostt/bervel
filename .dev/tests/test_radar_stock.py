"""Exact stock arithmetic; source-request counts cannot masquerade as inventory."""
from decimal import Decimal
import pytest

from bervel.core.errors import CoreError


def inventory(free='900',unit='шт',**specs):
    return {'kind':'mock','items':[{'sku':'P1','product':'Болт','free_quantity':free,'unit':unit,
        'specs':specs,'row':2,'as_of':None}]}


def position(quantity='1000',unit='шт',quote=None,**changes):
    return dict(sku='P1',exact_match=True,quantity=quantity,unit=unit,
                quote=quote or f'Болт: {quantity} {unit}')|changes


def test_threshold_boundary_uses_exact_decimals_without_rounding():
    from bervel.modules.tender_radar.stock import stock_check
    assert stock_check([position()],True,inventory(),Decimal('10'))['status']=='pass'
    assert stock_check([position()],True,inventory(),Decimal('9.99'))['status']=='insufficient'
    # 1/3 must not be rounded down to fit 33.33%.
    assert stock_check([position('3')],True,inventory('2'),Decimal('33.33'))['status']=='insufficient'


def test_repeated_sku_is_summed_within_one_tender():
    from bervel.modules.tender_radar.stock import stock_check
    checked=stock_check([position('600'),position('600',quote='Вторая строка Болт: 600 шт')],True,inventory('1000'),Decimal('10'))
    assert checked['status']=='insufficient'
    assert checked['checks'][0]['required']=='1200'
    assert checked['checks'][0]['available']=='1000'


@pytest.mark.parametrize('positions,complete,stock',[
    ([position()],True,None),([position(None)],True,inventory()),([position(unit='кг')],True,inventory()),
    ([position(exact_match=False)],True,inventory()),([position(sku=None)],True,inventory()),
    ([position()],False,inventory()),([position('0')],True,inventory()),
    ([position(quote='Болт М16: 1000 шт')],True,inventory(thread='М20')),
    ([],True,inventory()),
])
def test_unknown_or_unproven_data_never_pass_quantity(positions,complete,stock):
    from bervel.modules.tender_radar.stock import stock_check
    assert stock_check(positions,complete,stock,Decimal('100'))['status']=='unknown'


def test_same_known_specs_and_unit_synonyms_are_accepted():
    from bervel.modules.tender_radar.stock import stock_check
    p=position('1000','штук','Болт ГОСТ 7798-70 М16×50 8.8, цинк: 1000 штук')
    stock=inventory('1000',standard='ГОСТ7798-70',thread='М16',length_mm='50',strength='8.8',coating='цинк')
    assert stock_check([p],True,stock,Decimal('0'))['status']=='pass'


@pytest.mark.parametrize('quote',[
    'Болт ГОСТ 7798-70 М16×60 8.8, цинк: 50 шт',
    'Болт ГОСТ 7798-70 М16×50 8.8, без цинкового покрытия: 50 шт',
])
def test_wrong_dimensions_or_negated_coating_cannot_be_proved_by_other_numbers(quote):
    from bervel.modules.tender_radar.stock import stock_check
    stock=inventory('1000',standard='ГОСТ7798-70',thread='М16',length_mm='50',strength='8.8',coating='цинк')
    assert stock_check([position('50',quote=quote)],True,stock,Decimal('0'))['status']=='unknown'


@pytest.mark.parametrize('quote',['Болт: -50 шт','Болт: 1e50 шт','Болт: 0.050 шт'])
def test_quantity_evidence_never_matches_a_number_suffix(quote):
    from bervel.modules.tender_radar.stock import quantity_in_quote
    assert not quantity_in_quote('50','шт',quote)


def test_washer_diameter_needs_dimension_context_not_a_piece_count():
    from bervel.modules.tender_radar.stock import stock_check
    stock=inventory('1000',standard='ГОСТ11371-78',thread='16')
    wrong=position('16',quote='Шайба 16 шт, ГОСТ 11371-78, отверстие 20 мм')
    right=position('1000',quote='Шайба ГОСТ 11371-78, диаметр 16 мм: 1000 шт')
    assert stock_check([wrong],True,stock,Decimal('0'))['status']=='unknown'
    assert stock_check([right],True,stock,Decimal('0'))['status']=='pass'


def test_stock_import_preserves_mock_and_exact_values():
    from bervel.modules.tender_radar.infrastructure.stock import StockImporter
    data=StockImporter().parse('sku;product;free_quantity;unit;data_kind\nP1;Болт;1 000,25;кг;mock\n'.encode(),'stock.csv')
    assert data['kind']=='mock' and data['items'][0]['free_quantity']=='1000.25'
    assert data['items'][0]['row']==2


@pytest.mark.parametrize('csv',[
    'profile_id,product,source_request_quantity,source_unit\nP1,Болт,1000,шт\n',
    'sku,product,free_quantity,unit\nP1,Болт,-1,шт\n',
    'sku,product,free_quantity,unit\nP1,Болт,NaN,шт\n',
    'sku,product,free_quantity,unit\nP1,Болт,10,шт\nP1,Болт,20,шт\n',
    'sku,product,free_quantity,unit\nP1,Болт,1 5,шт\n',
])
def test_invalid_or_historical_stock_is_rejected(csv):
    from bervel.modules.tender_radar.infrastructure.stock import StockImporter
    with pytest.raises(CoreError):StockImporter().parse(csv.encode(),'stock.csv')


def test_blank_stock_count_is_unknown_not_zero():
    from bervel.modules.tender_radar.infrastructure.stock import StockImporter
    data=StockImporter().parse('sku,product,free_quantity,unit\nP1,Болт,,шт\n'.encode(),'stock.csv')
    assert data['items'][0]['free_quantity'] is None
