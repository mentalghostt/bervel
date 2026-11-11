from datetime import datetime, timezone

import pytest

from bervel.core.errors import CoreError


def test_fullness_cannot_be_inferred_from_identical_search_items():
    from bervel.modules.ai_tender_radar.completeness import check_completeness
    from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender
    item = {'Id': '1', 'OrderName': 'Болты', 'Currency': 'RUB'}
    card = {'Id': '1', 'Title': 'Болты', 'Lots': [{'JointObjects': [
        {'Name': 'Болт М12', 'Quantity': 10, 'Okei': 'шт', 'ItemPrice': 100}]}]}
    good = check_completeness(normalize_tender(item, card))
    assert good['requirements'] == good['economics'] == 'complete'
    assert good['schedule'] == 'unknown'
    card['Lots'][0]['JointObjects'] = []
    assert check_completeness(normalize_tender(item, card))['requirements'] == 'incomplete'


def test_candidate_collection_paginates_deduplicates_and_ranks_before_details():
    from bervel.modules.ai_tender_radar.selection import collect_candidates, rank_candidates
    class Source:
        simulated = True
        def search_page(self, phrase, page, start, end):
            return {'PageNumber': page, 'TotalCount': 51, 'Items': [
                {'Id': str(i), 'OrderName': 'Болт М12' if i == 50 else 'Обслуживание болтов'}
                for i in range(page * 50, min(51, page * 50 + 50))]}
    progress = []
    result = collect_candidates(Source(), ['болт', 'болт ГОСТ'], search_max=4,
                                checkpoint={}, progress=lambda checkpoint: progress.append(checkpoint.copy()))
    assert len(result['candidates']) == 51
    assert result['search_attempts'] == 4
    assert result['stop_reason'] == 'search_exhausted'
    profile = {'products': [{'id': 'B', 'name': 'Болт М12', 'enabled': True}], 'experimental_products': []}
    ranked = rank_candidates(result['candidates'], profile)
    assert ranked[0]['id'] == '50'
    assert len(progress) >= 4


def test_live_source_is_refused_before_any_network_call():
    from bervel.modules.ai_tender_radar.selection import collect_candidates
    class Source:
        simulated = False
        def search_page(self, *args):
            pytest.fail('Production source must not be queried')
    with pytest.raises(CoreError) as error:
        collect_candidates(Source(), ['болт'], search_max=2, checkpoint={}, progress=lambda _: None)
    assert error.value.code == 'SIMULATION_REQUIRED'


def test_source_terms_do_not_use_application_deadline_as_delivery_date():
    from bervel.modules.ai_tender_radar.completeness import extract_terms
    card = {'ApplicationDeadline': '2026-11-10T00:00:00+03:00', 'Lots': []}
    assert extract_terms(card)['delivery_deadline'] is None
    card['Lots'] = [{'Customers': [{'StartContractDate': '2026-11-11T00:00:00+03:00',
                                  'EndContractDate': '2026-11-30T00:00:00+03:00'}]}]
    assert extract_terms(card)['delivery_deadline'] is None
    card['Lots'][0]['Customers'][0]['EndContractDate'] = 'Поставка до 2026-11-30T00:00:00+03:00'
    assert extract_terms(card)['delivery_deadline'] == '2026-11-30T00:00:00+03:00'


def test_one_known_delivery_date_does_not_complete_other_mandatory_lots():
    from bervel.modules.ai_tender_radar.completeness import extract_terms, check_completeness
    from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender
    position = {'Name': 'Болт М12', 'Quantity': 10, 'Okei': 'шт', 'ItemPrice': 100}
    card = {'Id': 'a', 'Title': 'Болты', 'Lots': [
        {'Customers': [{'EndContractDate': 'Поставка до 2026-11-30T00:00:00+03:00', 'Objects': [position]}]},
        {'Customers': [{'Objects': [position]}]}]}
    tender = normalize_tender({'Id': 'a', 'Currency': 'RUB'}, card)
    tender['terms'] = extract_terms(card)
    checked = check_completeness(tender)
    assert checked['requirements'] == checked['economics'] == 'complete'
    assert checked['schedule'] == 'unknown'
    assert checked['complete'] is False


def test_deep_search_is_partitioned_before_inaccessible_page():
    from bervel.modules.ai_tender_radar.selection import collect_candidates
    class Source:
        simulated = True
        def search_page(self, phrase, page, start, end):
            wide = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days > 200
            assert page < 200
            return {'PageNumber': page, 'TotalCount': 15000 if wide else 1,
                    'Items': [{'Id': str(int(datetime.fromisoformat(start).timestamp())) + '-' + str(i), 'OrderName': 'Болт'}
                              for i in range(50 if wide else 1)]}
    value = collect_candidates(Source(), ['болт'], search_max=3, checkpoint={}, progress=lambda _: None)
    assert value['search_attempts'] == 3
    assert len(value['branches']) == 3
    assert value['stop_reason'] == 'search_exhausted'


def test_document_hidden_requirement_and_unread_sixth_document_prevent_complete():
    from bervel.modules.ai_tender_radar.completeness import check_completeness
    tender = {'positions': [{'quantity':'10','currency':'RUB','item_price':'100','position_price':None}],
              'limitations': [], 'terms': {'delivery_deadline':'2026-12-31T00:00:00+03:00'},
              'documents': [{'text':'Обязательное покрытие: #####'}]}
    assert check_completeness(tender)['requirements'] == 'incomplete'
    tender['documents'] = [{'text':'Открытые данные'}] * 5
    tender['source_card'] = {'Docs':[{}] * 6}
    assert check_completeness(tender)['requirements'] == 'unknown'
    assert check_completeness(tender)['complete'] is False


def test_search_queries_cover_families_before_individual_skus():
    from bervel.modules.ai_tender_radar.selection import search_queries
    profile = {'products': [
        {'id':'a','name':'Болт М12','attributes':{},'enabled':True},
        {'id':'b','name':'Болт М16','attributes':{},'enabled':True},
        {'id':'c','name':'Гайка М12','attributes':{},'enabled':True}],
        'include_experimental':False,'experimental_products':[]}
    queries = search_queries(profile)
    assert queries[:2] == ['Болт','Гайка']
    assert 'Болт М12' in queries
