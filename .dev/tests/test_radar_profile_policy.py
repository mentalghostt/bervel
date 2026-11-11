from copy import deepcopy
from datetime import datetime, timezone

import pytest

from bervel.modules.ai_tender_radar.profile_policy import (
    card_actuality, profile_metrics, search_fingerprint, trade_type,
)


NOW = datetime(2026, 11, 9, 9, tzinfo=timezone.utc)


@pytest.mark.parametrize('state', ['primary', 'accepted', 'needs_data', 'irrelevant', 'failed'])
def test_actuality_is_independent_of_analysis_state(state):
    card = {'state': state, 'brief': {'ApplicationDeadline': '2026-11-10T09:00:00Z'}}
    assert card_actuality(card, NOW) == {'actuality': 'active', 'inactivity_reason': None}


@pytest.mark.parametrize('deadline', ['2026-11-09T09:00:00Z', '2026-11-09T11:59:59+03:00'])
def test_expiry_includes_exact_deadline_and_planned_tenders(deadline):
    card = {'brief': {'IsPlanning': True, 'ApplicationDeadline': deadline}}
    assert card_actuality(card, NOW)['inactivity_reason'] == 'deadline_expired'


def test_new_brief_overrides_old_full_metadata_without_mutation():
    card = {'tender': {'source_card': {'ApplicationDeadline': '2026-11-01T00:00:00Z',
        'InitialSum': {'Price': 100, 'Currency': 'RUB'}, 'TypeOfTrading': 1}},
        'brief': {'ApplicationDeadline': '2026-11-20T00:00:00Z', 'MaxPrice': 250,
                  'Currency': 'RUB', 'TypeOfTrading': 2}}
    before = deepcopy(card)
    metrics = profile_metrics(card, NOW)
    assert metrics['actuality'] == 'active'
    assert metrics['price'] == '250'
    assert metrics['trade_type'] == 'fz223'
    assert card == before


def test_disclosed_unknown_price_does_not_reuse_stale_full_price():
    card = {'tender': {'source_card': {'InitialSum': {'Price': 100, 'Currency': 'RUB'}}},
            'brief': {'MaxPrice': None, 'Currency': None}}
    assert profile_metrics(card, NOW)['price'] is None


@pytest.mark.parametrize('field,reason', [('source_missing', 'source_missing'),
                                        ('source_unavailable', 'source_unavailable')])
def test_missing_and_unavailable_are_not_deleted(field, reason):
    card = {field: True, 'brief': {'ApplicationDeadline': '2026-11-20T00:00:00Z'}}
    assert card_actuality(card, NOW) == {'actuality': 'inactive', 'inactivity_reason': reason}


@pytest.mark.parametrize('flag', ['IsCancelled', 'IsAbandoned'])
def test_source_stage_flags_close_tender(flag):
    assert card_actuality({'brief': {flag: True}}, NOW)['inactivity_reason'] == 'stage_closed'


def test_closed_detail_response_has_its_own_truthful_reason():
    assert card_actuality({'source_closed': True}, NOW)['inactivity_reason'] == 'stage_closed'


@pytest.mark.parametrize('value', [None, '', 'not a date'])
def test_unknown_deadline_does_not_manufacture_expiry(value):
    assert card_actuality({'brief': {'ApplicationDeadline': value}}, NOW)['actuality'] == 'active'


@pytest.mark.parametrize('raw,expected', [(1, 'fz44'), (2, 'fz223'), (5, 'pp615'),
    (8, 'commercial'), (9, 'small'), (3, 'commercial_small'), ('СНГ', 'cis'),
    (None, 'unknown'), (999, 'unknown'), (True, 'unknown')])
def test_source_trade_types_are_not_guessed(raw, expected):
    assert trade_type({'brief': {'TypeOfTrading': raw}}) == expected


def test_title_is_not_evidence_for_trade_type():
    assert trade_type({'brief': {'OrderName': 'Поставка по 44-ФЗ в СНГ'}}) == 'unknown'


def test_keyword_fingerprint_matches_semantic_set_but_not_changed_search():
    assert search_fingerprint(['  БОЛТ  м8 ', 'гайка', 'гайка']) == search_fingerprint(['Гайка', 'болт м8'])
    assert search_fingerprint(['болт']) != search_fingerprint(['гайка'])
