"""Independent execution contracts; no simulator corpus or labels are used."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
from threading import Lock
from time import perf_counter, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bervel.core.errors import CoreError
from bervel.modules.ai_tender_radar.infrastructure.archive import read_archive


def tender(key, *, quantity='10', name='Болт М8', incomplete=False):
    return {'id': key, 'title': 'Комплектующие', 'number': None, 'link': None,
        'positions': [{'id': '1', 'lot': 1, 'customer': None, 'name': name,
            'quantity': quantity, 'unit': 'шт', 'item_price': '20',
            'position_price': str(int(quantity) * 20), 'currency': 'RUB', 'characteristics': []}],
        'limitations': [{'code': 'SOURCE_UNKNOWN'}] if incomplete else [],
        'terms': {'delivery_deadline': '2099-12-01T00:00:00+00:00', 'source_schedule_complete': True}}


class Repository:
    def __init__(self):
        self.saved, self.state, self.pages, self.updates = {}, {}, [], []

    def cards(self, run_id):
        return [{'candidate_id': key, 'data': deepcopy(value)} for key, value in self.saved.items()]

    def update(self, scope, run_id, state):
        self.state = deepcopy(state)
        self.updates.append({key: state.get(key, 0) for key in ('detail_attempts', 'model_attempts')})

    def add_card(self, run_id, key, card):
        self.saved.setdefault(key, deepcopy(card))

    def prepare_card(self, run_id, key, value):
        self.saved[key].update(tender=deepcopy(value), input_finalized=True)

    def finalize_card(self, run_id, key, card):
        assert self.saved[key]['raw_tender']
        self.saved[key].update(deepcopy(card))

    def archive_page(self, run_id, page):
        self.pages.append(deepcopy(page))
        return str(len(self.pages))


class Source:
    simulated, key = True, 'independent-fake'

    def __init__(self, cards, *, detail_quota=10000, search_quota=10000):
        self.cards = {card['id']: card for card in cards}
        self.fetches, self.searches, self.limit_reads = [], [], 0
        self.detail_quota, self.search_quota, self.fail_at = detail_quota, search_quota, None

    def read_limits(self):
        self.limit_reads += 1
        return [{'LimitGroup': 'search', 'AvailableRequests': self.search_quota},
                {'LimitGroup': 'purchase', 'AvailableRequests': self.detail_quota}]

    def search_page(self, phrase, page, start, end):
        self.searches.append((phrase, page, start, end))
        keys = list(self.cards)
        return {'Items': [{'Id': key, 'OrderName': 'Болт М8'} for key in keys[page * 50:(page + 1) * 50]],
                'PageNumber': page, 'TotalCount': len(keys)}

    def fetch_tender(self, key, search_item):
        self.fetches.append(key)
        if key == self.fail_at:
            raise CoreError('KONTUR_RATE_LIMIT', 'rate limit')
        return deepcopy(self.cards[key])


class Model:
    def __init__(self, repository, delay=0):
        self.repository, self.delay = repository, delay
        self.lock, self.active, self.maximum, self.calls = Lock(), 0, 0, 0

    def generate(self, request_id, prompt, **kwargs):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.calls += 1
        try:
            key = request_id.removeprefix('run-')[37:].rsplit('-', 1)[0]
            assert self.repository.saved[key].get('raw_tender')
            sleep(self.delay)
            batch = json.loads(prompt.split('\nВХОД\n', 1)[1])
            positions = [{'id': pos['id'], 'matches': [
                {'sku': candidate['sku'], 'evidence': [{'requirement_id': req, 'field': next(iter(fields))}
                    for req, fields in candidate['evidence'].items()]}
                for candidate in pos['candidates']]} for pos in batch]
            return {'model': 'independent-fake', 'answer': json.dumps({'positions': positions})}
        finally:
            with self.lock:
                self.active -= 1


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr('bervel.modules.ai_tender_radar.review.review_card',
                        lambda card, *args, **kwargs: {'status': 'saved', 'coverage': card['analysis']['coverage_percent']})
    repository = Repository()
    source = Source([tender('one')])
    model = Model(repository)
    pipeline = SimpleNamespace(repository=repository, sources={'test_api': source}, model=model,
        jobs=SimpleNamespace(heartbeat=lambda job_id: None), parse_archive=read_archive)
    job = {'job_id': uuid4(), 'actor_id': 'operator', 'data_scope': 'private', 'operation': {'kind': 'selection'}}
    run = {'run_id': job['job_id'], 'created_at': datetime(2026, 11, 5, tzinfo=timezone.utc),
        'data': {'selection_version': 3, 'source': 'test_api', 'name': 'Исторический срез', 'checkpoint': {}}}
    profile = {'products': [{'id': 'bolt', 'sku': 'B8', 'name': 'Болт М8', 'enabled': True}],
               'experimental_products': [], 'include_experimental': False}
    stock = {'data_kind': 'real', 'rows': [{'row': 1, 'sku': 'B8', 'product': 'Болт М8',
        'free_quantity': '10', 'unit': 'шт', 'unit_cost': '5', 'currency': 'RUB',
        'as_of': '2026-11-05T00:00:00+00:00', 'attributes': {}}]}
    return None, pipeline, job, run, profile, stock


def execute(env):
    module, pipeline, job, run, profile, stock = env
    module = importlib.import_module('bervel.modules.ai_tender_radar.named_runs')
    return module.execute_named(pipeline, job, run, profile, stock)


@pytest.mark.parametrize(('maximum', 'pages', 'details'), [(1, 1, 1), (None, 2, 51)])
def test_unlimited_simulator_respects_only_the_run_maximum(environment, monkeypatch, maximum, pages, details):
    _, pipeline, _, run, _, _ = environment
    source = pipeline.sources['test_api']
    source.cards = {str(index): tender(str(index)) for index in range(51)}
    source.read_limits = lambda: []
    run['data'].update(stage_versions={'selection': 'ai-selection-v1'}, budgets={'detail': maximum})
    module = importlib.import_module('bervel.modules.ai_tender_radar.named_runs')
    monkeypatch.setattr(module, 'model_search_queries', lambda *args, **kwargs: ['болт'])
    monkeypatch.setattr(module, 'rank_briefs_with_model', lambda candidates, *args, **kwargs: candidates)
    monkeypatch.setattr(module, '_analyze', lambda card, *args: dict(card, state='accepted'))

    execute(environment)

    assert [search[1] for search in source.searches] == list(range(pages))
    assert len(source.fetches) == details
    assert pipeline.repository.state['state'] == 'complete'


@pytest.mark.parametrize('bad_ranking', [False, True])
def test_ai_selection_orders_queries_search_ranking_and_quota(environment, monkeypatch, bad_ranking):
    _, pipeline, _, run, _, _ = environment
    module = importlib.import_module('bervel.modules.ai_tender_radar.named_runs')
    source = pipeline.sources['test_api']
    source.cards['two'] = tender('two')
    source.read_limits = lambda: [
        {'LimitGroup': group, 'TotalLimit': 200, 'AvailableRequests': 200}
        for group in ('search', 'purchase')
    ]
    run['data'].update(stage_versions={'selection': 'ai-selection-v1'}, budgets={'detail': 1})
    phrases = ['болт', 'метизы']
    events = []

    def keywords(profile, model, request_id, *, heartbeat):
        assert not source.searches and not source.fetches
        events.append('keywords')
        return phrases

    def ranking(candidates, profile, model, request_id, *, heartbeat):
        assert [entry[0] for entry in source.searches] == [phrases]
        assert not source.fetches
        assert [card['id'] for card in candidates] == ['one', 'two']
        assert pipeline.repository.state['checkpoint']['search_keywords'] == phrases
        events.append('rank')
        if bad_ranking:
            raise CoreError('MODEL_INVALID_OUTPUT', 'Некорректный рейтинг')
        return list(reversed(candidates))

    monkeypatch.setattr(module, 'model_search_queries', keywords, raising=False)
    monkeypatch.setattr(module, 'rank_briefs_with_model', ranking, raising=False)
    if bad_ranking:
        with pytest.raises(CoreError) as error:
            execute(environment)
        assert error.value.code == 'MODEL_INVALID_OUTPUT'
        assert pipeline.repository.state['detail_attempts'] == 0
        assert not source.fetches
    else:
        execute(environment)
        assert pipeline.repository.state['detail_attempts'] == 1
        assert source.fetches == ['two']
        assert pipeline.repository.saved['two']['analysis'] is not None
        assert pipeline.repository.state['counts']['looked'] == 2
    assert events == ['keywords', 'rank']


def test_ai_selection_skips_candidate_closed_after_ranking(environment, monkeypatch):
    _, pipeline, _, run, _, _ = environment
    module = importlib.import_module('bervel.modules.ai_tender_radar.named_runs')
    source = pipeline.sources['test_api']
    source.cards['two'] = tender('two')
    source.read_limits = lambda: [
        {'LimitGroup': group, 'TotalLimit': 200, 'AvailableRequests': 200}
        for group in ('search', 'purchase')
    ]
    run['data'].update(stage_versions={'selection': 'ai-selection-v1'}, budgets={'detail': 1})
    closed_after_ranking = set()

    def ranking(candidates, *args, **kwargs):
        assert all(card['state'] == 'candidate' for card in candidates)
        closed_after_ranking.add('one')
        return candidates

    monkeypatch.setattr(module, 'model_search_queries', lambda *a, **kw: ['болт'])
    monkeypatch.setattr(module, 'rank_briefs_with_model', ranking)
    monkeypatch.setattr(module, 'brief_is_closed', lambda brief, _: brief['Id'] in closed_after_ranking)
    execute(environment)
    assert source.fetches == ['two']
    assert pipeline.repository.saved['one']['selection']['reason'] == 'CLOSED_PROCEDURE'


def test_ai_selection_resumes_search_and_ranks_only_new_candidates(environment, monkeypatch):
    _, pipeline, job, run, _, _ = environment
    module = importlib.import_module('bervel.modules.ai_tender_radar.named_runs')
    source = pipeline.sources['test_api']
    source.search_quota = 1
    source.cards['two'] = tender('two')
    first = {'id': 'one', 'brief': {'Id': 'one', 'OrderName': 'Болт М8'}, 'state': 'candidate'}
    ranked_first = dict(first, rank=70)
    pipeline.repository.saved['one'] = {'state': 'accepted', 'raw_tender': tender('one')}
    run['data'].update(
        stage_versions={'selection': 'ai-selection-v1'}, state='failed', stop_reason='search_budget',
        detail_attempts=1, ranked_candidates=[ranked_first],
        checkpoint={'search_keywords': ['болт'], 'search_attempts': 1, 'candidates': [first],
                    'branches': [{'phrase': 'Общий запрос', 'page': 1, 'done': False}],
                    'stop_reason': 'search_budget'},
    )
    job['operation']['kind'] = 'resume'

    def second_page(phrases, page, start, end):
        assert phrases == ['болт'] and page == 1
        return {'Items': [{'Id': 'two', 'OrderName': 'Болт М8'}], 'PageNumber': 1, 'TotalCount': 51}

    def rank_new(candidates, *args, **kwargs):
        assert [row['id'] for row in candidates] == ['two']
        return [dict(candidates[0], rank=90)]

    monkeypatch.setattr(source, 'search_page', second_page)
    monkeypatch.setattr(module, 'rank_briefs_with_model', rank_new)
    execute(environment)
    assert source.fetches == ['two']
    state = pipeline.repository.state
    assert state['checkpoint']['search_attempts'] == 2
    assert state['ranked_candidates'][1] == ranked_first
    assert state['stop_reason'] == 'search_exhausted'


def test_selected_stages_reach_search_and_cancelled_cards_are_analyzed(environment):
    from bervel.modules.ai_tender_radar.result_metrics import result_metrics

    _, pipeline, _, run, _, _ = environment
    source = pipeline.sources['test_api']
    run['data']['purchase_stages'] = ['finished', 'cancelled']
    requested = []

    def search(phrase, page, start, end, *, purchase_statuses):
        requested.append(purchase_statuses)
        return {'Items': [{'Id': 'one', 'OrderName': 'Болт М8', 'IsCancelled': True,
                           'ApplicationDeadline': '2026-01-01T00:00:00+00:00'}], 'TotalCount': 1, 'PageNumber': 0}

    fetch = source.fetch_tender

    def fetch_cancelled(key, search_item, *, allow_cancelled):
        assert allow_cancelled is True
        return fetch(key, search_item)

    source.search_page = search
    source.fetch_tender = fetch_cancelled
    execute(environment)
    assert requested == [[3, 5]]
    assert source.fetches == ['one']
    assert pipeline.repository.saved['one']['analysis'] is not None
    assert result_metrics(pipeline.repository.saved['one'])['stage'] == 'cancelled'
    assert pipeline.repository.state['search_scope']['purchase_stages'] == ['finished', 'cancelled']


def test_kontur_client_transmits_statuses_and_opens_explicitly_selected_cancellations(monkeypatch):
    from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient
    from bervel.modules.ai_tender_radar.source_contract import SIMULATOR_KEY

    client = KonturClient(SIMULATOR_KEY, base_url='http://127.0.0.1:8765')
    search_requests = []

    def request(path, body=None):
        if path.endswith('/search'):
            search_requests.append(body['PurchaseStatuses'])
            return {'Items': [], 'PageNumber': 0, 'TotalCount': 0}
        return {'Id': 'one', 'Title': 'Отменённая закупка', 'CancelReason': 'Отмена', 'Lots': []}

    monkeypatch.setattr(client, '_request', request)
    client.search_page('болт', 0, '2026-01-01T00:00:00Z', '2026-11-06T00:00:00Z', purchase_statuses=[2, 3, 5])
    client.search_page('болт', 0, '2026-01-01T00:00:00Z', '2026-11-06T00:00:00Z')
    assert search_requests == [[2, 3, 5], [1]]
    with pytest.raises(CoreError, match='отменена'):
        client.fetch_tender('one')
    assert client.fetch_tender('one', allow_cancelled=True)['source_card']['CancelReason'] == 'Отмена'


def test_named_execution_saves_real_coverage_and_preserves_name(environment):
    execute(environment)
    repo = environment[1].repository
    assert repo.state['name'] == 'Исторический срез'
    assert repo.saved['one']['state'] == 'accepted'
    assert repo.saved['one']['analysis']['coverage_percent'] == '100'
    assert repo.saved['one']['analysis']['mock'] is False
    assert repo.state['scope_complete'] is True
    assert repo.state['counts'] == {'accepted': 1, 'needs_data': 0, 'irrelevant': 0, 'failed': 0, 'looked': 1}
    assert environment[1].sources['test_api'].searches[0][2] == '1970-01-01T00:00:00Z'


def test_current_run_spends_only_requested_details_and_keeps_matching_lots(environment):
    from bervel.modules.ai_tender_radar.prefilter import CANDIDATE_PRIORITY_VERSION
    from bervel.modules.ai_tender_radar.runs import _stage_versions

    _, pipeline, _, run, _, _ = environment
    source = pipeline.sources['test_api']
    source.cards['one']['positions'].append(dict(tender('one', quantity='300', name='Краска')['positions'][0], id='2', lot=2))
    source.cards['two'] = tender('two')
    source.read_limits = lambda: [{'LimitGroup': group, 'TotalLimit': 200, 'AvailableRequests': 200}
                                  for group in ('search', 'purchase')]
    run['data'].update(stage_versions=_stage_versions(CANDIDATE_PRIORITY_VERSION), budgets={'detail': 1})
    execute(environment)
    assert source.fetches == ['one']
    assert pipeline.repository.state['detail_attempts'] == 1
    assert pipeline.repository.state['stop_reason'] == 'requested_detail_limit'
    result = pipeline.repository.saved['one']
    assert result['catalog_match_percent'] == '50'
    assert result['state'] == 'accepted'
    assert result['selection']['reason'] == 'CATALOG_LOTS_CONFIRMED'


def test_current_run_records_unread_documents_even_on_a_preliminary_reject(environment):
    from bervel.modules.ai_tender_radar.prefilter import CANDIDATE_PRIORITY_VERSION
    from bervel.modules.ai_tender_radar.runs import _stage_versions

    _, pipeline, _, run, _, _ = environment
    source = pipeline.sources['test_api']
    source.cards['one'] = tender('one', name='Краска')
    source.cards['one']['source_card'] = {'Lots': [{}], 'Docs': [{'Url': 'https://example.test/specification'}]}
    run['data']['stage_versions'] = _stage_versions(CANDIDATE_PRIORITY_VERSION)
    execute(environment)
    result = pipeline.repository.saved['one']
    assert result['input_finalized'] is True
    assert result['tender']['document_extraction']['error']['code'] == 'DOCUMENTS_NOT_FULLY_READ'


@pytest.mark.parametrize(('saved_version', 'version', 'score'), [(None, 2, None), (2, 2, None), (3, 3, 100), (4, 4, 100)])
def test_named_execution_uses_the_production_version_saved_on_the_run(environment, saved_version, version, score):
    _, pipeline, _, run, profile, stock = environment
    if saved_version is not None:
        run['data']['stage_versions'] = {'production': saved_version}
    terms = pipeline.sources['test_api'].cards['one']['terms']
    terms.update(start_at='2000-01-01T00:00:00+00:00', delivery_days=0,
                 logistics_cost='0', other_cost='0', tax_comparable=True)
    stock['rows'][0]['as_of'] = None
    profile['scoring_policy'] = {'max_age_by_source': {'stock': '0'}}

    execute(environment)

    card = pipeline.repository.saved['one']
    assert card['state'] == 'accepted'
    assert card['fulfillment']['version'] == version
    assert ('stress' in card['fulfillment']) == (version == 2)
    assert card['assessment']['score'] == score


def test_named_run_uses_experimental_sku_only_for_production_shortage(environment):
    from bervel.modules.company_settings.models import stock_for_profile
    module, pipeline, job, run, profile, stock = environment
    run['data']['stage_versions'] = {'production': 3}
    demand = pipeline.sources['test_api'].cards['one']
    now = datetime.now(timezone.utc)
    demand['terms'].update(start_at=(now - timedelta(days=1)).isoformat(),
        delivery_deadline=(now + timedelta(days=10)).isoformat(), delivery_days=0,
        logistics_cost='0', other_cost='0', tax_comparable=True)
    demand['positions'].append(dict(demand['positions'][0], id='2', name='Шайба Test-E-900', quantity='2',
                                    position_price='40'))
    profile['products'][0].update(unit='шт', attributes={})
    profile.update(include_experimental=True, experimental_products=[
        {'id': 'exp', 'sku': 'Шайба Test-E-900', 'name': 'Шайба экспериментальная',
         'unit': 'шт', 'attributes': {}, 'enabled': True}],
        resources=[{'group_id': 'g', 'calendar': [0, 1, 2, 3, 4], 'default_daily_capacity': '1'}],
        production=[{'product_id': 'exp', 'resource_group_id': 'g', 'units_per_workday': '100',
            'production_unit_cost': '5', 'currency': 'RUB', 'setup_workdays': '0',
            'material_ready_at': now.isoformat(), 'evidence': 'Factory data'}])

    execute((module, pipeline, job, run, profile, stock_for_profile(stock, profile)))

    card = pipeline.repository.saved['one']
    assert card['state'] == 'accepted', (card['selection'], card['analysis']['positions'], card['completeness'])
    assert 80 <= float(card['analysis']['coverage_percent']) < 100
    assert card['analysis']['positions'][1]['covered'] == '0'
    assert card['fulfillment']['production_allocations'][0]['sku'] == 'EXP:exp'
    assert card['fulfillment']['full_economics']['cost'] == '60'


def test_proven_low_bound_rejects_without_model_or_fictional_analysis(environment):
    environment[1].sources['test_api'].cards['one'] = tender('one', quantity='100')
    execute(environment)
    card = environment[1].repository.saved['one']
    assert card['state'] == 'irrelevant'
    assert card['analysis'] is None
    assert card['selection']['reason'] == 'STOCK_UPPER_BOUND_BELOW_80'
    assert environment[1].model.calls == 0


def test_current_selection_analyzes_full_catalog_fit_even_with_low_stock(environment):
    _, pipeline, _, run, _, _ = environment
    run['data']['stage_versions'] = {'selection': 'catalog-prefilter-v4'}
    pipeline.sources['test_api'].cards['one'] = tender('one', quantity='100')
    execute(environment)
    card = pipeline.repository.saved['one']
    assert card['catalog_match_percent'] == '100'
    assert card['analysis']['coverage_percent'] == '10'
    assert card['assessment'] is not None
    assert card['review']['coverage'] == '10'


def test_named_stock_coverage_allocates_only_across_matching_lots(environment):
    from bervel.modules.ai_tender_radar.prefilter import LOT_COVERAGE_SELECTION_VERSION
    _, pipeline, _, run, _, _ = environment
    run['data']['stage_versions'] = {'selection': LOT_COVERAGE_SELECTION_VERSION}
    value = tender('one')
    bolt = value['positions'][0]
    value['positions'] = [
        dict(bolt, id='unmatched-competing-bolt', lot=1),
        dict(bolt, id='unmatched-line', lot=1, name='Болт М9'),
        dict(bolt, id='matching-lot', lot=2, quantity='20', position_price='400'),
        dict(bolt, id='unmatched-lot-three', lot=3, name='Болт М10'),
        dict(bolt, id='unmatched-lot-four', lot=4, name='Болт М11'),
    ]
    pipeline.sources['test_api'].cards['one'] = value
    execute(environment)
    card = pipeline.repository.saved['one']
    assert card['catalog_match_percent'] == '25'
    assert card['stock_coverage_by_piece'] == {'percent': '50', 'incomplete': False}
    assert card['analysis'] is not None


def test_new_run_requests_compact_review_after_partial_catalog_analysis(environment, monkeypatch):
    from bervel.modules.ai_tender_radar.prefilter import LOT_COVERAGE_SELECTION_VERSION
    from bervel.modules.ai_tender_radar.review import COMPACT_PROMPT_VERSION
    _, pipeline, _, run, _, _ = environment
    run['data']['stage_versions'] = {'selection': LOT_COVERAGE_SELECTION_VERSION, 'review': COMPACT_PROMPT_VERSION}
    value = tender('one')
    bolt = value['positions'][0]
    value['positions'] = [dict(bolt, quantity='20'), dict(bolt, id='outside', lot=2, name='Болт М9')]
    pipeline.sources['test_api'].cards['one'] = value
    reviews = []

    def review_card(card, *args, prompt_version):
        reviews.append((card['catalog_match_percent'], prompt_version))
        return {'state': 'complete'}

    monkeypatch.setattr('bervel.modules.ai_tender_radar.review.review_card', review_card)
    execute(environment)
    assert reviews == [('50', COMPACT_PROMPT_VERSION)]
    assert pipeline.repository.saved['one']['review']['state'] == 'complete'


def test_unknown_unit_prefilter_runs_actual_matching_without_review(environment):
    value = tender('one', quantity='100')
    value['positions'][0]['unit'] = 'неизвестная единица'
    environment[1].sources['test_api'].cards['one'] = value
    execute(environment)
    card = environment[1].repository.saved['one']
    assert card['analysis']['coverage_percent'] is None
    assert card['review'] is None
    assert card['state'] == 'needs_data'
    assert environment[1].model.calls == 1


def test_has_no_200_limit_and_uses_bounded_analysis_workers(environment):
    module, pipeline, job, run, profile, stock = environment
    pipeline.sources['test_api'] = Source([tender(str(index)) for index in range(205)])
    pipeline.model.delay = .002
    execute(environment)
    assert pipeline.repository.state['counts']['accepted'] == 205
    assert pipeline.repository.state['model_attempts'] == 205
    assert 1 <= pipeline.model.maximum <= 4
    assert len(pipeline.repository.pages) == 5
    assert pipeline.repository.state['unverified_count'] == 0
    attempts = [item['model_attempts'] for item in pipeline.repository.updates]
    assert attempts == sorted(attempts)


def test_independent_cards_overlap_model_work_with_at_most_four_active(environment):
    pipeline = environment[1]
    pipeline.sources['test_api'] = Source([tender(str(index)) for index in range(10)])
    pipeline.model.delay = .05
    execute(environment)
    assert 2 <= pipeline.model.maximum <= 4
    assert pipeline.repository.state['model_attempts'] == 10


def test_resume_reuses_finalized_raw_card_and_does_not_refetch_or_reset_quota(environment):
    module, pipeline, job, run, profile, stock = environment
    pipeline.sources['test_api'] = Source([tender('one'), tender('two')], detail_quota=1)
    execute(environment)
    assert pipeline.repository.state['scope_complete'] is False
    assert pipeline.repository.state['unverified_count'] == 1
    assert pipeline.repository.state['stop_reason'] == 'detail_quota'
    run['data'] = deepcopy(pipeline.repository.state)
    execute(environment)
    assert pipeline.sources['test_api'].fetches == ['one']
    assert pipeline.sources['test_api'].limit_reads == 1
    assert pipeline.repository.state['detail_attempts'] == 1
    assert pipeline.repository.state['model_attempts'] == 1


def test_source_error_drains_started_work_and_saves_resumable_partial(environment):
    module, pipeline, job, run, profile, stock = environment
    source = pipeline.sources['test_api'] = Source([tender('one'), tender('two')])
    source.fail_at = 'two'
    with pytest.raises(CoreError, match='rate limit'):
        execute(environment)
    assert pipeline.repository.saved['one']['state'] == 'accepted'
    assert pipeline.repository.state['state'] == 'failed'
    assert pipeline.repository.state['unverified_count'] == 1
    assert pipeline.repository.state['detail_attempts'] == 2


def test_resume_finishes_saved_input_even_after_new_fetch_quota_is_spent(environment):
    module, pipeline, job, run, profile, stock = environment
    value = tender('two')
    candidate = lambda key: {'id': key, 'brief': {'Id': key, 'OrderName': 'Болт М8'}, 'state': 'candidate'}
    run['data'].update(limits=[], effective_budgets={'search': 0, 'detail': 1}, detail_attempts=1,
        checkpoint={'candidates': [candidate('one'), candidate('two')], 'branches': [],
                    'search_attempts': 0, 'stop_reason': 'search_exhausted'})
    pipeline.repository.saved['two'] = {'analysis_id': str(uuid4()), 'candidate_id': 'two',
        'brief': candidate('two')['brief'], 'raw_tender': deepcopy(value), 'tender': value,
        'input_finalized': True, 'evaluated_at': '2026-11-05T00:00:00+00:00', 'state': 'analyzing',
        'analysis': None, 'fulfillment': None, 'assessment': None, 'review': None}
    execute(environment)
    assert pipeline.repository.saved['two']['state'] == 'accepted'
    assert pipeline.sources['test_api'].fetches == []
    assert pipeline.repository.state['unverified_count'] == 1


def test_missing_source_records_no_fake_complete_scope(environment):
    environment[1].sources = {}
    with pytest.raises(CoreError):
        execute(environment)
    assert environment[1].repository.state['scope_complete'] is False


def test_incomplete_actual_analysis_keeps_unknown_coverage(environment):
    environment[1].sources['test_api'].cards['one'] = tender('one', incomplete=True)
    execute(environment)
    card = environment[1].repository.saved['one']
    assert card['analysis']['coverage_percent'] is None
    assert card['state'] == 'needs_data'
    assert card['review'] is None


def test_search_quota_resume_does_not_fetch_existing_page_again(environment):
    pipeline, run = environment[1], environment[3]
    pipeline.sources['test_api'] = Source([tender(str(index)) for index in range(51)], search_quota=1)
    execute(environment)
    assert len(pipeline.repository.saved) == 50
    assert pipeline.repository.state['scope_complete'] is False
    assert pipeline.repository.state['stop_reason'] == 'search_budget'
    assert pipeline.repository.state['unverified_count'] == 0
    run['data'] = deepcopy(pipeline.repository.state)
    execute(environment)
    assert len(pipeline.sources['test_api'].searches) == 1
    assert pipeline.repository.state['checkpoint']['search_attempts'] == 1
    assert pipeline.repository.state['model_attempts'] == 50


def test_closed_detail_is_rejected_without_stopping_remaining_cards(environment):
    pipeline = environment[1]
    source = pipeline.sources['test_api'] = Source([tender('closed'), tender('one')])
    fetch = source.fetch_tender
    def load(key, search_item):
        if key == 'closed':
            raise CoreError('KONTUR_TENDER_CLOSED', 'closed since search')
        return fetch(key, search_item)
    source.fetch_tender = load
    execute(environment)
    assert pipeline.repository.saved['closed']['state'] == 'irrelevant'
    assert pipeline.repository.saved['one']['state'] == 'accepted'
    assert pipeline.repository.state['scope_complete'] is True


def test_changed_file_hash_does_not_analyze_anything(environment):
    pipeline, run = environment[1], environment[3]
    run['data'].update(source='file', source_artifact_id=str(uuid4()), source_sha256='pinned')
    pipeline.artifacts = SimpleNamespace(read=lambda *args: SimpleNamespace(content=b'[]', metadata={'sha256': 'other'}))
    with pytest.raises(CoreError) as error:
        execute(environment)
    assert error.value.code == 'STALE_REVISION'
    assert pipeline.model.calls == 0
    assert pipeline.repository.saved == {}


def test_exact_80_percent_is_eligible_even_with_zero_score(environment, monkeypatch):
    environment[5]['rows'][0]['free_quantity'] = '8'
    module = importlib.import_module('bervel.modules.ai_tender_radar.named_runs')
    monkeypatch.setattr(module, 'score_assessment', lambda *args: {'score': 0})
    execute(environment)
    card = environment[1].repository.saved['one']
    assert card['analysis']['coverage_percent'] == '80'
    assert card['state'] == 'accepted'
    assert card['assessment']['score'] == 0


def test_unknown_model_outcome_is_counted_once_without_automatic_retry(environment):
    pipeline, run = environment[1], environment[3]
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        raise CoreError('MODEL_TIMEOUT', 'unknown outcome')
    pipeline.model.generate = timeout
    execute(environment)
    card = pipeline.repository.saved['one']
    assert card['analysis']['model_error']['code'] == 'MODEL_TIMEOUT'
    assert card['analysis']['coverage_percent'] is None
    assert card['state'] == 'needs_data'
    run['data'] = deepcopy(pipeline.repository.state)
    execute(environment)
    assert len(calls) == 1
    assert pipeline.repository.state['model_attempts'] == 1


def test_analysis_failure_reports_incomplete_scope_reason(environment, monkeypatch):
    module = importlib.import_module('bervel.modules.ai_tender_radar.named_runs')
    def fail(*args, **kwargs):
        raise CoreError('ANALYSIS_UNAVAILABLE', 'analysis failed')
    monkeypatch.setattr(module, 'calculate_analysis', fail)
    execute(environment)
    state = environment[1].repository.state
    assert state['counts']['failed'] == 1
    assert state['scope_complete'] is False
    assert state['stop_reason'] == 'card_failures'


def test_file_reads_pinned_owned_artifact_and_never_uses_source(environment):
    module, pipeline, job, run, profile, stock = environment
    content = json.dumps({'Id': 'archived', 'Title': 'Болт М8', 'InitialSum': {'Currency': 'RUB'},
        'Lots': [{'JointObjects': [{'Name': 'Болт М8', 'Quantity': 10, 'Okei': 'шт',
            'ItemPrice': 20, 'PositionPrice': 200}]}]}).encode()
    stamp = hashlib.sha256(content).hexdigest()
    identifier = uuid4()
    run['data'].update(source='file', source_artifact_id=str(identifier), source_sha256=stamp)
    reads = []
    def read(actor, ref):
        reads.append((actor, ref))
        return SimpleNamespace(content=content, metadata={'sha256': stamp})
    pipeline.artifacts = SimpleNamespace(read=read)
    execute(environment)
    assert pipeline.repository.saved['archived']['analysis']['coverage_percent'] == '100'
    assert pipeline.sources['test_api'].limit_reads == 0
    assert pipeline.sources['test_api'].fetches == []
    assert reads[0][0].data_scope == 'private'
    assert reads[0][1].artifact_id == identifier


def test_missing_test_source_never_falls_back_to_live(environment):
    pipeline = environment[1]
    pipeline.sources = {'kontur_api': Source([tender('live')])}
    with pytest.raises(CoreError):
        execute(environment)
    assert pipeline.sources['kontur_api'].fetches == []


def test_file_requires_injected_parser_without_source_or_artifact_calls(environment):
    pipeline, run = environment[1], environment[3]
    pipeline.parse_archive = None
    run['data'].update(source='file', source_artifact_id=str(uuid4()), source_sha256='pinned')
    with pytest.raises(CoreError) as error:
        execute(environment)
    assert error.value.code == 'FILE_SOURCE_NOT_CONFIGURED'
    assert pipeline.sources['test_api'].limit_reads == 0
    assert pipeline.sources['test_api'].fetches == []
    assert pipeline.repository.saved == {}


def test_mock_ten_card_functional_timing(environment):
    pipeline = environment[1]
    pipeline.sources['test_api'] = Source([tender(str(index)) for index in range(10)])
    start = perf_counter()
    execute(environment)
    assert pipeline.repository.state['counts']['accepted'] == 10
    print(f'Independent fake-model functional 10 cards: {perf_counter() - start:.3f}s, '
          f'{pipeline.repository.state["model_attempts"]} calls; not a real-model benchmark')
