"""Existential catalogue admission checks; independent of source corpora."""
import pytest
from bervel.modules.ai_tender_radar.prefilter import CatalogPrefilter
from test_ai_tender_radar_analysis import need, row, stock, tender


@pytest.mark.parametrize('unit', ['шт', 'л', 'м', 'кг', 'упаковка'])
def test_foreign_goods_do_not_match_a_supplier_name_or_require_unit_conversion(unit):
    warehouse = stock(row('B', 'Болт М12', free='1000'))
    demand = tender(need('Мыло жидкое', 100, unit))
    demand['title'] = 'Поставка для ООО Болт М12'
    result = CatalogPrefilter(warehouse, {}).check(demand)
    assert result['state'] == 'reject'
    assert result['reason'] == 'NO_PROVABLE_CATALOG_MATCH'


def test_all_requirements_must_be_known_before_no_match_gate_can_reject():
    warehouse = stock(row('B', 'Болт М12', free='1000'))
    demand = tender(need('Мыло жидкое', 100, 'л'), need('Болты', 100))
    result = CatalogPrefilter(warehouse, {}).check(demand)
    assert result['state'] == 'unknown'


def test_one_proven_line_in_mixed_inventory_prevents_no_match_rejection():
    warehouse = stock(row('B', 'Болт М12', free='1000'))
    demand = tender(need('Мыло жидкое', 100, 'л'), need('Болт М12', 100))
    result = CatalogPrefilter(warehouse, {}).check(demand)
    assert result['reason'] != 'NO_PROVABLE_CATALOG_MATCH'
    assert result['state'] == 'unknown'


def test_unknown_quantity_is_not_a_complete_inventory_even_without_catalogue_match():
    warehouse = stock(row('B', 'Болт М12', free='1000'))
    demand = tender(need('Мыло жидкое', 100, 'л'))
    demand['positions'][0]['quantity'] = None
    assert CatalogPrefilter(warehouse, {}).check(demand)['state'] == 'unknown'
