from test_ai_radar_production import case, evaluate


def test_catalogue_product_without_warehouse_sku_can_use_confirmed_production():
    data = case(covered='0', rate='100')
    data[1]['rows'] = []
    data[2]['positions'][0]['matches'][0]['sku'] = 'CAT:product'
    data[3]['products'][0]['sku'] = None
    plan = evaluate(data)
    assert plan['status'] == 'feasible'
    assert plan['production_allocations'][0]['sku'] == 'CAT:product'
