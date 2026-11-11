from threading import RLock
from types import SimpleNamespace
from uuid import uuid4

from bervel.modules.ai_tender_radar.brief_ranking import AI_SELECTION_VERSION
from bervel.modules.ai_tender_radar.named_runs import _analyze
from bervel.modules.ai_tender_radar.prefilter import CANDIDATE_PRIORITY_VERSION, CatalogPrefilter
from test_ai_tender_radar_analysis import SelectingModel, need, row, stock, tender


def analyze(demand, warehouse, model, selection=AI_SELECTION_VERSION):
    raw_card = {'analysis_id': 'test-analysis', 'candidate_id': 'abc',
                'evaluated_at': '2026-11-06T11:00:00+00:00', 'tender': demand}
    repository = SimpleNamespace(prepare_card=lambda *_: None)
    prefilter = CatalogPrefilter(warehouse, {}) if selection != AI_SELECTION_VERSION else None
    return _analyze(raw_card, SimpleNamespace(repository=repository), uuid4(), warehouse, {},
                    prefilter, model, RLock(), lambda: None, {'selection': selection})


def test_ai_selection_checks_compatible_position_in_an_incompatible_lot():
    demand = tender(need('Болт М12', 20), need('Шайба М12'))
    warehouse = stock(row('B12', 'Болт М12'))
    legacy_model = SelectingModel()
    legacy = analyze(demand, warehouse, legacy_model, CANDIDATE_PRIORITY_VERSION)
    assert legacy['analysis'] is None
    assert not legacy_model.calls

    model = SelectingModel()
    checked = analyze(demand, warehouse, model)
    assert len(model.calls) == 1
    bolt, washer = checked['analysis']['positions']
    assert [match['sku'] for match in bolt['matches']] == ['B12']
    assert bolt['covered'] == '10' and bolt['shortage'] == '10'
    assert washer['stock_reason'] == 'NO_COMPATIBLE_SKU'
    assert checked['catalog_match_percent'] == '0'
    assert checked['state'] == 'irrelevant'


def test_ai_selection_keeps_an_unproven_position_unknown():
    demand = tender(need('Болт М12', Characteristics=[{'Name': 'Покрытие', 'Values': [{'Value': 'Цинк'}]}]))
    warehouse = stock(row('B12', 'Болт М12', coating_options='Цинк'))
    model = SelectingModel()
    checked = analyze(demand, warehouse, model)
    assert len(model.calls) == 1
    assert checked['catalog_match_percent'] is None
    assert checked['stock_coverage_by_piece'] == {'percent': None, 'incomplete': True}
    assert checked['state'] == 'needs_data'
    assert checked['analysis']['positions'][0]['stock_status'] == 'unconfirmed'
