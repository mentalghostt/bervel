from datetime import date
from decimal import Decimal as D

def test_shared_stock_is_allocated_once_and_technical_evidence_is_verified():
    from bervel.product_analysis import prepare_candidates, validate_selected_evidence, allocate_stock, weighted_piece_coverage
    rows = [dict(sku='B', product='Болт М12', unit='шт', free_quantity='100', attributes={}, unit_cost='10', currency='RUB')]
    demands = [dict(id=str(i), name='Болт М12', quantity='80', unit='шт', characteristics=[]) for i in range(2)]
    matches = {}
    for demand in demands:
        prepared = prepare_candidates(demand, rows)
        assert prepared.candidates[0]['sku'] == 'B'
        matches[demand['id']] = validate_selected_evidence(demand, rows, [dict(sku='B', evidence=prepared.candidates[0]['evidence'])])
    allocation = allocate_stock(demands, dict(rows=rows), matches)
    assert allocation.covered == {'0': D('80'), '1': D('20')}
    assert weighted_piece_coverage(allocation).percent == D('62.5')
    bad = validate_selected_evidence(demands[0], rows, [dict(sku='B', evidence={'family': {'product': 'Гайка'}})])
    assert not bad.by_sku
    assert bad.uncertain

def test_unknown_conversion_is_not_zero():
    from bervel.product_analysis import allocate_stock
    from bervel.product_analysis.models import MatchResult
    demand = dict(id='x', name='Болт', unit='шт', quantity='10', characteristics=[])
    rows = [dict(sku='B', product='Болт', unit='кг', free_quantity='10', attributes={})]
    result = allocate_stock([demand], dict(rows=rows), {'x': MatchResult({'B': []}, False)})
    assert 'x' in result.unconfirmed
    assert result.covered.get('x') is None
    assert result.coverage.incomplete

def test_calendar_shares_capacity_and_preserves_fractional_work():
    from bervel.product_analysis import production_workdays, schedule_work
    assert production_workdays(D('5'), D('10'), D('.1'), rate_factor=D('1')) == D('.6')
    jobs = [dict(position_id=str(i), resource_group_id='R', release=date(2026,11,10), deadline=date(2026,11,13), work=D('1.5')) for i in range(2)]
    result = schedule_work(jobs, {'R': {}}, capacity=lambda r,d: D('1'))
    assert result.ready_at == {'0': date(2026,11,12), '1': date(2026,11,13)}
    assert sum((x['work'] for x in result.schedule), D('0')) == D('3')

def test_all_catalog_rows_are_examined_beyond_radar_model_batch_limit():
    from bervel.product_analysis import prepare_candidates
    demand=dict(id='L',name='Болт М12',quantity='1',unit='шт',characteristics=[])
    rows=[dict(sku=str(i),product='Болт М12',unit='шт',free_quantity='0',attributes={}) for i in range(40)]
    assert len(prepare_candidates(demand,rows).candidates)==40
