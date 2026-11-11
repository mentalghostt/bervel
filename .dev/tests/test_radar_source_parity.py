"""Transport and recovery parity using explicit synthetic source responses.

No real API or database is used. Both actual KonturClient variants consume the
same wire fixtures; the shared profile scenario owns validation/checkpoints.
"""
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import json
import re
from threading import RLock
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient
from bervel.modules.ai_tender_radar.source_contract import BASE_URL, SIMULATOR_KEY
from bervel.modules.ai_tender_radar.review import review_card as actual_review_card
from test_radar_profile_refresh import refresh, execute, next_refresh, no_retry_wait


def brief(key, *, deadline='2099-12-01T12:00:00+03:00', planned=False):
    return {'Id': key, 'OrderName': 'Болт М8', 'ApplicationDeadline': deadline,
            'IsPlanning': planned, 'IsCancelled': False, 'IsAbandoned': False,
            'TypeOfTrading': 1, 'MaxPrice': '200', 'Currency': 'RUB'}


def raw_card(key):
    return {'Id': key, 'Title': 'Болт М8', 'NotificationNumber': 'fixture-01',
            'InitialSum': {'Price': '200', 'Currency': 'RUB'},
            'Lots': [{'JointObjects': [{'Name': 'Болт М8', 'Quantity': '10',
                'Okei': 'шт', 'ItemPrice': '20', 'PositionPrice': '200', 'Characteristics': []}]}]}


class WireSource:
    """A bounded fake HTTP transport, not a replacement scenario/normalizer."""
    def __init__(self, *, total=1, expired=False):
        self.total, self.expired = total, expired
        self.calls, self.search_error, self.detail_error = [], None, None
        self.limits_error = None
        self.page_mutation = None
        self.card_mutation = None
        self.limits = [{'LimitGroup': group, 'AvailableRequests': 200,
                        'TotalLimit': 200, 'IsPeriodic': True}
                       for group in ('search', 'purchase')]

    def open(self, request, *, timeout):
        assert timeout == 25
        path = urlsplit(request.full_url).path
        payload = json.loads(request.data) if request.data is not None else None
        self.calls.append((request.get_method(), path, payload))
        if path == '/external/v1/limitGroups':
            if self.limits_error:
                raise HTTPError(request.full_url, self.limits_error, 'fixture', {}, BytesIO(b'{}'))
            value = self.limits
        elif path == '/external/v1/search':
            if self.search_error:
                raise HTTPError(request.full_url, self.search_error, 'fixture', {}, BytesIO(b'{}'))
            page = payload['PageNumber']
            deadline = '2000-01-01T00:00:00Z' if self.expired else '2099-12-01T12:00:00+03:00'
            value = {'PageNumber': page, 'TotalCount': self.total,
                     'Items': [brief(f'wire-{n:05}', deadline=deadline)
                               for n in range(page * 50, min(self.total, (page + 1) * 50))]}
            if self.page_mutation:
                value = self.page_mutation(value)
        elif path.startswith('/external/v1/purchases/'):
            if self.detail_error:
                raise HTTPError(request.full_url, self.detail_error, 'fixture', {}, BytesIO(b'{}'))
            value = raw_card(path.rsplit('/', 1)[-1])
            if self.card_mutation:
                value = self.card_mutation(value)
        else:
            raise AssertionError(f'Unexpected network path: {path}')
        return BytesIO(json.dumps(value, ensure_ascii=False).encode())


def adapter(source_kind, wire):
    if source_kind == 'test_api':
        return KonturClient(SIMULATOR_KEY, base_url='http://localhost:18765', opener=wire, min_interval=0)
    return KonturClient('fixture-official-key', base_url=BASE_URL, allow_configured_api=True,
                        opener=wire, min_interval=0)


def attach(env, wire):
    source_kind = env.job['operation']['profile_source']
    env.job['operation']['refresh_version'] = 'incremental-profile-v2'
    env.pipeline.sources[source_kind] = adapter(source_kind, wire)
    return wire


@pytest.mark.parametrize('source_kind', ['test_api', 'kontur_api'])
@pytest.mark.parametrize(('page', 'total', 'size'), [(0, 0, 0), (0, 49, 49), (0, 50, 50),
                                                    (1, 51, 1), (199, 9999, 49), (199, 10000, 50)])
def test_both_adapters_send_the_same_explicit_search_contract(source_kind, page, total, size):
    wire = WireSource(total=total)
    result = adapter(source_kind, wire).search_page(['болт', 'гайка'], page,
        '1970-01-01T00:00:00Z', '2026-11-09T12:00:00Z', purchase_statuses=[1, 4])
    assert result['PageNumber'] == page and result['TotalCount'] == total
    assert len(result['Items']) == size
    assert wire.calls == [('POST', '/external/v1/search', {
        'Text': ['болт', 'гайка'], 'PurchaseStatuses': [1, 4], 'PageNumber': page,
        'DateTimeFrom': '1970-01-01T00:00:00Z', 'DateTimeTo': '2026-11-09T12:00:00Z',
        'Attachments': True, 'MaxPriceNone': True, 'AllowForeignCurrency': True})]


@pytest.mark.parametrize('source_kind', ['test_api', 'kontur_api'])
def test_detail_normalization_is_shared_and_does_not_invent_document_text(source_kind):
    wire = WireSource()
    wire.card_mutation = lambda card: dict(card, Docs=[{'Url': 'https://example.invalid/private.pdf'}])
    # Real attachments are deliberately unavailable here; this test cannot reach a live URL.
    if source_kind == 'test_api':
        wire.card_mutation = None
    result = adapter(source_kind, wire).fetch_tender('wire-00000', search_item=brief('wire-00000'))
    assert result['positions'][0]['quantity'] == '10'
    assert result['positions'][0]['position_price'] == '200'
    assert result['positions'][0]['unit'] == 'шт'
    assert not result.get('documents')
    assert len(wire.calls) == 1


@pytest.mark.parametrize(('total', 'expected_count', 'complete'), [(0, 0, True), (49, 49, True),
    (50, 50, True), (9999, 9999, True), (10000, 10000, False)])
def test_full_search_boundaries_have_the_same_scope_semantics(refresh, total, expected_count, complete):
    wire = attach(refresh, WireSource(total=total, expired=True))
    refresh.job['operation']['detail_max'] = 0
    execute(refresh)
    assert len(refresh.repository.saved) == expected_count
    assert refresh.repository.state['scope_complete'] is complete
    pages = [payload['PageNumber'] for _, path, payload in wire.calls if path.endswith('/search')]
    assert pages == list(range(max(1, (total + 49) // 50)))
    assert refresh.pipeline.model.calls == []


@pytest.mark.parametrize('corruption', ['wrong_page', 'short_page', 'changed_total', 'duplicate'])
def test_bad_or_unstable_pages_never_claim_a_complete_search(refresh, corruption):
    wire = attach(refresh, WireSource(total=51, expired=True))
    refresh.job['operation']['detail_max'] = 0

    def mutate(value):
        if value['PageNumber'] != 1:
            return value
        if corruption == 'wrong_page':
            value['PageNumber'] = 0
        elif corruption == 'short_page':
            value['Items'] = []
        elif corruption == 'changed_total':
            value['TotalCount'] = 50
            value['Items'] = []
        else:
            value['Items'][0]['Id'] = 'wire-00000'
        return value

    wire.page_mutation = mutate
    if corruption in {'wrong_page', 'short_page'}:
        with pytest.raises(CoreError, match='KONTUR_INVALID_RESPONSE'):
            execute(refresh)
        assert not refresh.repository.state.get('scope_complete', False)
    else:
        execute(refresh)
        assert refresh.repository.state['scope_complete'] is False
    assert not any(path.startswith('/external/v1/purchases/') for _, path, _ in wire.calls)


@pytest.mark.parametrize(('http_status', 'code'), [(401, 'KONTUR_AUTH_ERROR'), (403, 'KONTUR_ACCESS_DENIED'),
    (429, 'KONTUR_RATE_LIMIT'), (502, 'KONTUR_UNAVAILABLE')])
def test_unknown_search_is_not_repeated_on_resume_even_for_retryable_http_status(refresh, http_status, code):
    wire = attach(refresh, WireSource())
    wire.search_error = http_status
    with pytest.raises(CoreError, match=code):
        execute(refresh)
    refresh.run['data'] = deepcopy(refresh.repository.state)
    wire.search_error = None
    with pytest.raises(CoreError, match='SOURCE_REQUEST_OUTCOME_UNKNOWN'):
        execute(refresh)
    assert sum(path.endswith('/search') for _, path, _ in wire.calls) == 1


@pytest.mark.parametrize(('http_status', 'state'), [(404, 'unavailable'), (502, 'unknown')])
def test_detail_outcomes_preserve_paid_reservation_and_primary_score(refresh, http_status, state):
    wire = attach(refresh, WireSource())
    wire.detail_error = http_status
    if http_status == 502:
        with pytest.raises(CoreError, match='KONTUR_UNAVAILABLE'):
            execute(refresh)
    else:
        execute(refresh)
    card = refresh.repository.saved['wire-00000']
    assert card['detail_request_state'] == state
    assert card['priority_score'] == 50
    assert not card['details_loaded']
    assert sum(path.startswith('/external/v1/purchases/') for _, path, _ in wire.calls) == 1


@pytest.mark.parametrize(('search_quota', 'detail_quota', 'search_calls'), [(0, 200, 0), (2, 0, 2)])
def test_search_and_detail_quotas_are_independent_for_both_sources(refresh, search_quota, detail_quota, search_calls):
    wire = attach(refresh, WireSource(total=51, expired=True))
    wire.limits[0]['AvailableRequests'] = search_quota
    wire.limits[1]['AvailableRequests'] = detail_quota
    execute(refresh)
    assert sum(path.endswith('/search') for _, path, _ in wire.calls) == search_calls
    assert not any(path.startswith('/external/v1/purchases/') for _, path, _ in wire.calls)
    assert refresh.pipeline.model.calls == []


def test_empty_provider_limits_are_only_an_unlimited_simulator_fallback(refresh):
    wire = attach(refresh, WireSource(total=0))
    wire.limits = []
    if refresh.job['operation']['profile_source'] == 'kontur_api':
        with pytest.raises(CoreError, match='KONTUR_INVALID_RESPONSE'):
            execute(refresh)
        assert len(wire.calls) == 1
    else:
        execute(refresh)
        assert refresh.repository.state['effective_budgets'] == {'search': 200, 'detail': 1}
        assert sum(path.endswith('/search') for _, path, _ in wire.calls) == 1


@pytest.mark.parametrize('stage', ['expired', 'cancelled', 'planned'])
def test_actuality_controls_detail_spending_without_source_specific_rules(refresh, stage):
    wire = attach(refresh, WireSource())

    def mutate(value):
        row = value['Items'][0]
        if stage == 'expired':
            row['ApplicationDeadline'] = '2000-01-01T00:00:00'  # Moscow source-local time.
        elif stage == 'cancelled':
            row['IsCancelled'] = True
        else:
            row['IsPlanning'] = True
        return value

    wire.page_mutation = mutate
    execute(refresh)
    loaded = refresh.repository.saved['wire-00000']
    if stage == 'planned':
        assert loaded['details_loaded']
        assert sum(path.startswith('/external/v1/purchases/') for _, path, _ in wire.calls) == 1
    else:
        assert loaded['metrics']['actuality'] == 'inactive'
        assert not loaded['details_loaded']
        assert refresh.pipeline.model.calls == []


def test_cancelled_detail_after_open_brief_is_closed_and_preserves_primary(refresh):
    wire = attach(refresh, WireSource())
    wire.card_mutation = lambda card: dict(card, CancelReason='Закупка отменена')
    execute(refresh)
    saved = refresh.repository.saved['wire-00000']
    assert saved['source_closed'] is True
    assert saved['detail_request_state'] == 'unavailable'
    assert saved['priority_score'] == 50
    assert not saved['details_loaded']


def test_successful_primary_priority_is_reused_after_new_full_refresh(refresh):
    wire = attach(refresh, WireSource())
    refresh.job['operation']['detail_max'] = 0
    execute(refresh)
    original = deepcopy(refresh.repository.saved['wire-00000']['priority_evidence'])
    before = len(refresh.pipeline.model.calls)
    next_refresh(refresh)
    execute(refresh)
    assert len(refresh.pipeline.model.calls) == before, 'A saved primary score was sent to the model again'
    assert refresh.repository.saved['wire-00000']['priority_evidence'] == original
    assert sum(path.endswith('/search') for _, path, _ in wire.calls) == 2


def test_new_primary_is_scored_without_resending_the_saved_primary(refresh):
    wire = attach(refresh, WireSource())
    refresh.job['operation']['detail_max'] = 0
    execute(refresh)
    wire.total = 2
    next_refresh(refresh)
    execute(refresh)
    assert refresh.pipeline.model.ranking_ids == ['wire-00000', 'wire-00001']
    assert {row['priority_score'] for row in refresh.repository.saved.values()} == {50}


def test_unchanged_full_inputs_reuse_matching_review_and_rating_in_the_next_refresh(refresh):
    wire = attach(refresh, WireSource())
    execute(refresh)
    saved = deepcopy(refresh.repository.saved['wire-00000'])
    before = list(refresh.pipeline.model.calls)
    next_refresh(refresh)
    execute(refresh)
    current = refresh.repository.saved['wire-00000']
    assert refresh.pipeline.model.calls == before, 'Unchanged full inputs repeated a completed model stage'
    assert current['analysis'] == saved['analysis']
    assert current['individual_rating']['score'] == 83
    assert current['analysis_id'] == saved['analysis_id']
    assert sum(path.startswith('/external/v1/purchases/') for _, path, _ in wire.calls) == 1


def test_new_full_refresh_commits_local_metrics_before_source_failure_without_repeating_analysis(refresh):
    from bervel.modules.ai_tender_radar.profile_policy import profile_metrics
    wire = attach(refresh, WireSource())
    execute(refresh)
    original = deepcopy(refresh.repository.saved['wire-00000']['analysis'])
    before = len(refresh.pipeline.model.calls)
    refresh.stock['rows'][0]['free_quantity'] = '0'
    next_refresh(refresh)
    wire.limits_error = 502
    with pytest.raises(CoreError, match='KONTUR_UNAVAILABLE'):
        execute(refresh)
    current = refresh.repository.saved['wire-00000']
    assert current['analysis'] == original
    assert profile_metrics(current)['stock_coverage_percent'] == '0', 'Source I/O happened before the local metrics commit'
    assert len(refresh.pipeline.model.calls) == before


def test_missing_source_document_text_remains_unknown_without_any_download():
    from bervel.modules.ai_tender_radar.documents import enrich_tender
    from test_named_radar_execution import tender
    card = tender('not-downloaded')
    card['source_card'] = {'Docs': [{'Url': 'https://example.invalid/never-fetch.pdf'}]}

    class ForbiddenModel:
        def generate(self, *args, **kwargs):
            pytest.fail('Unavailable document text must not be invented by a model')

    result = enrich_tender(card, ForbiddenModel(), 'missing-text', lambda: None)
    assert result['document_extraction']['error']['code'] == 'DOCUMENTS_NOT_FULLY_READ'
    assert not result.get('documents')


def test_shared_text_stage_preparses_explicit_facts_for_both_source_identities(monkeypatch):
    from bervel.modules.ai_tender_radar import documents
    from bervel.modules.ai_tender_radar.named_runs import _analyze
    from test_named_radar_execution import Repository, tender
    from test_radar_profile_refresh import Model
    from types import SimpleNamespace
    from uuid import uuid4
    version = getattr(documents, 'SHARED_TEXT_PROMPT_VERSION', None)
    assert version is not None, 'The versioned shared saved-text stage is missing'
    outputs = []
    for source_kind in ('test_api', 'kontur_api'):
        repository = Repository()
        model = Model(repository)
        generate = model.generate

        def answer(request_id, prompt, **kwargs):
            if request_id.startswith('documents-'):
                return {'model': 'fixture', 'answer': json.dumps({'terms': {key: None for key in documents.TERM_FIELDS},
                    'requirements': [], 'inventory': {'complete': None, 'rows': []}})}
            return generate(request_id, prompt, **kwargs)

        model.generate = answer
        value = tender('same-saved-input')
        value['documents'] = [{'filename': 'facts.txt', 'url': 'https://saved.invalid/facts.txt',
            'text': 'Логистика, RUB: 5\nПрочие обязательные расходы, RUB: 0\nСрок доставки, дней: 2\nНалоговая база: цены и затраты сопоставимы'}]
        value['source_card'] = {'Docs': [{'Url': 'https://saved.invalid/facts.txt'}], 'Lots': [{}]}
        original = deepcopy(value)
        card = {'candidate_id': 'same-saved-input', 'analysis_id': str(uuid4()), 'tender': value,
                'evaluated_at': datetime(2026, 11, 9, tzinfo=timezone.utc).isoformat()}
        profile = {'products': [{'id': 'bolt', 'sku': 'B8', 'name': 'Болт М8', 'enabled': True}]}
        stock = {'data_kind': 'real', 'rows': [{'row': 1, 'sku': 'B8', 'product': 'Болт М8',
            'free_quantity': '10', 'unit': 'шт', 'unit_cost': '5', 'currency': 'RUB',
            'as_of': '2026-11-09T00:00:00+00:00', 'attributes': {}}]}
        pipeline = SimpleNamespace(repository=repository, source=source_kind)
        monkeypatch.setattr('bervel.modules.ai_tender_radar.review.review_card', lambda *args, **kwargs: {'state': 'complete'})
        repository.saved['same-saved-input'] = deepcopy(card)
        result = _analyze(card, pipeline, uuid4(), stock, profile, None, model, RLock(), lambda: None,
                          {'selection': 'ai-selection-v1', 'production': 4, 'documents': version})
        assert result['tender']['terms']['logistics_cost'] == '5'
        assert result['tender']['terms']['other_cost'] == '0'
        assert result['tender']['terms']['tax_comparable'] is True
        assert value == original
        outputs.append(result['tender']['terms'])
    assert outputs[0] == outputs[1]


def test_explicit_document_preparse_is_idempotent():
    from bervel.modules.ai_tender_radar.documents import prepare_saved_document_texts
    from bervel.modules.ai_tender_radar.completeness import enrich_from_documents
    texts = [{'url': 'https://saved.invalid/facts.txt', 'text': 'Логистика, RUB: 5'}]
    # The transport may have already parsed the texts; the shared stage must
    # converge to the same evidence as an input that only contains saved text.
    value = {'terms': {}, 'source_card': {'Lots': [{}]}, 'documents': texts}
    once = prepare_saved_document_texts(deepcopy(value))
    twice = prepare_saved_document_texts(enrich_from_documents(deepcopy(once), texts))
    assert twice == once, 'Transport preparse and shared preparse must not duplicate evidence'


@pytest.mark.parametrize(('proved_stock', 'percent', 'incomplete'), [('2', '20', True), ('10', '100', False)])
def test_new_unchecked_sku_marks_partial_proved_coverage_incomplete_but_does_not_weaken_full_coverage(
        proved_stock, percent, incomplete):
    from test_radar_metrics_refresh import metrics_case, with_origin, calculate
    data = with_origin(metrics_case(free=proved_stock))
    data[2]['rows'].append(dict(data[2]['rows'][0], sku='NEW-UNREVIEWED', free_quantity='8'))
    original = deepcopy(data)
    result = calculate(data)
    assert data == original
    assert result['coverage'] == {'percent': percent, 'incomplete': incomplete}
    assert {entry['sku'] for entry in result['lines'][0]['allocations']} == {'B12'}


def test_public_metrics_overlay_does_not_mix_new_line_costs_with_old_aggregate_economics():
    from bervel.modules.ai_tender_radar.metrics_refresh import project_metrics
    from test_radar_metrics_refresh import metrics_case, with_origin, calculate
    card, profile, stock = with_origin(metrics_case())
    frozen = deepcopy(card['analysis'])
    stock['rows'][0].update(free_quantity='8', unit_cost='6')
    card['metrics_projection'] = calculate((card, profile, stock))
    public = project_metrics(card)
    assert card['analysis'] == frozen
    assert public['analysis']['positions'][0]['economics']['revenue'] == '80'
    assert public['analysis']['positions'][0]['economics']['cost'] == '48'
    assert public['analysis']['economics']['revenue'] == '80'
    assert public['analysis']['economics']['cost'] == '48'
    assert public['analysis']['economics']['profit'] == '32'
    assert public['analysis']['economics']['margin_percent'] == '40'


def test_one_transient_matching_failure_is_deferred_until_all_fresh_cards_finish(refresh):
    wire = attach(refresh, WireSource(total=12))
    refresh.job['operation']['detail_max'] = 12
    generate = refresh.pipeline.model.generate
    matching = []

    def generated(request_id, prompt, **kwargs):
        if request_id.startswith('run-'):
            key = int(re.search(r'-wire-(\d{5})-', request_id)[1])
            matching.append(key)
            if key == 0 and matching.count(0) == 1:
                raise CoreError('MODEL_UNAVAILABLE', 'temporary matching outage')
        return generate(request_id, prompt, **kwargs)

    refresh.pipeline.model.generate = generated
    execute(refresh)
    assert set(matching[:12]) == set(range(12)), 'A failed card blocked or retried ahead of fresh siblings'
    assert matching.count(0) == 2
    assert len(matching) == 13
    assert refresh.repository.state['state'] == 'complete'
    assert all(card['individual_rating']['status'] == 'complete' for card in refresh.repository.saved.values())
    assert len({path for _, path, _ in wire.calls if path.startswith('/external/v1/purchases/')}) == 12
    assert sum(path.startswith('/external/v1/purchases/') for _, path, _ in wire.calls) == 12


def test_transient_review_failure_retries_only_review_after_the_analysis_is_saved(refresh, monkeypatch):
    wire = attach(refresh, WireSource())
    monkeypatch.setattr('bervel.modules.ai_tender_radar.review.review_card', actual_review_card)
    generate = refresh.pipeline.model.generate
    counts = {'matching': 0, 'review': 0, 'rating': 0}

    def generated(request_id, prompt, **kwargs):
        if request_id.startswith('run-'):
            counts['matching'] += 1
        elif request_id.startswith('review-'):
            counts['review'] += 1
            if counts['review'] == 1:
                raise CoreError('MODEL_UNAVAILABLE', 'temporary review outage')
            payload = json.loads(prompt.split('\nВХОД\n', 1)[1])
            response = {'recommendation': 'Складские изделия соответствуют закупке.\n\nНужно уточнить затраты на доставку.',
                'risks': [], 'assumptions': [], 'evidence_refs': ['card:/analysis', 'card:/fulfillment'],
                'calculated': payload['calculated'], 'decision': 'review', 'proposes_production': False}
            return {'model': 'fixture-review', 'answer': json.dumps({'action': 'final', 'ref': '', 'offset': 0,
                'length': 12000, 'query': '', 'notes': '', 'response': response})}
        elif request_id.startswith('card-rating-'):
            counts['rating'] += 1
        return generate(request_id, prompt, **kwargs)

    refresh.pipeline.model.generate = generated
    execute(refresh)
    assert counts == {'matching': 1, 'review': 2, 'rating': 1}
    card = refresh.repository.saved['wire-00000']
    assert card['analysis_completed_refresh_id']
    assert card['review']['state'] == 'complete'
    assert card['individual_rating']['score'] == 83
    assert sum(path.startswith('/external/v1/purchases/') for _, path, _ in wire.calls) == 1


def test_process_death_after_calculation_commit_resumes_review_without_matching_or_source_replay(refresh):
    from uuid import uuid4

    class ProcessDeath(BaseException):
        pass

    wire = attach(refresh, WireSource())
    upsert = refresh.profiles.upsert_card

    def committed_then_died(*args):
        value = upsert(*args)
        if value.get('analysis') and not value.get('review') and not value['analysis'].get('model_error'):
            raise ProcessDeath()
        return value

    refresh.profiles.upsert_card = committed_then_died
    with pytest.raises(ProcessDeath):
        execute(refresh)
    card = refresh.repository.saved['wire-00000']
    assert card['analysis'] and not card.get('review')
    before = deepcopy(card['analysis'])
    calls = [key for key in refresh.pipeline.model.calls if key.startswith('run-')]
    assert len(calls) == 1
    old = refresh.job
    refresh.run['data'] = deepcopy(refresh.repository.state)
    refresh.job = dict(old, job_id=uuid4(), operation=dict(old['operation'],
        refresh_id=old['operation'].get('refresh_id') or str(old['job_id']), resume_of=str(old['job_id'])))
    refresh.profiles.upsert_card = upsert
    execute(refresh)
    assert [key for key in refresh.pipeline.model.calls if key.startswith('run-')] == calls
    assert refresh.repository.saved['wire-00000']['analysis'] == before
    assert refresh.repository.saved['wire-00000']['individual_rating']['score'] == 83
    assert sum(path.startswith('/external/v1/purchases/') for _, path, _ in wire.calls) == 1


def test_successful_shared_document_retry_clears_only_its_previous_extraction_failure():
    from bervel.modules.ai_tender_radar import documents
    from test_named_radar_execution import tender
    value = tender('retry-documents')
    value['source_card'] = {'Lots': [{}], 'Docs': [{'Url': 'https://saved.invalid/facts.txt'}]}
    value['documents'] = [{'url': 'https://saved.invalid/facts.txt', 'text': 'Логистика, RUB: 5'}]
    value['limitations'] = [{'code': 'SOURCE_UNKNOWN'}]

    class Model:
        def __init__(self):
            self.calls = 0

        def generate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise CoreError('MODEL_UNAVAILABLE', 'temporary document outage')
            return {'model': 'fixture', 'answer': json.dumps({'terms': {key: None for key in documents.TERM_FIELDS},
                    'requirements': [], 'inventory': {'complete': None, 'rows': []}})}

    model = Model()
    first = documents.enrich_tender(value, model, 'documents-retry', lambda: None,
                                   prompt_version=documents.SHARED_TEXT_PROMPT_VERSION)
    assert first['document_extraction']['state'] == 'unknown'
    second = documents.enrich_tender(first, model, 'documents-retry', lambda: None,
                                    prompt_version=documents.SHARED_TEXT_PROMPT_VERSION)
    assert second['document_extraction']['state'] == 'complete'
    assert second['limitations'] == [{'code': 'SOURCE_UNKNOWN'}]
    assert model.calls == 2
