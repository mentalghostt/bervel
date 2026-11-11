"""HTTP-only simulator corpus checks; truth is never passed to the ranker."""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.client import HTTPConnection
import json
from threading import Thread

import pytest

from kontur_simulator import TEST_KEY, create_server


FIXED_DATE = '2026-11-05T00:00:00+03:00'


@contextmanager
def selection_server(**options):
    settings = {'profile': 'selection', 'seed': 7319, 'today': FIXED_DATE, 'max_requests_per_second': 0}
    settings.update(options)
    with create_server(port=0, **settings) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()

        def request(method, path, body=None, *, key=TEST_KEY):
            connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                connection.request(method, path, None if body is None else json.dumps(body),
                                   {'X-Kontur-Apikey': key, 'Content-Type': 'application/json'})
                response = connection.getresponse()
                payload = response.read()
                return response.status, json.loads(payload) if 'application/json' in response.getheader('Content-Type', '') else payload.decode('utf-8')
            finally:
                connection.close()

        try:
            yield server, request
        finally:
            server.shutdown()
            thread.join()


def search(text='закупка', page=0):
    return {'Text': [text], 'PageNumber': page, 'DateTimeFrom': '2025-11-05T00:00:00+03:00',
            'DateTimeTo': FIXED_DATE, 'Attachments': True, 'MaxPriceNone': True,
            'AllowForeignCurrency': True, 'PurchaseStatuses': [1]}


def test_selection_profile_is_fixed_seeded_and_hidden_truth_is_not_on_wire():
    with selection_server() as (server, request):
        status, first = request('POST', '/external/v1/search', search())
        assert status == 200 and len(first['Items']) == 50 and first['TotalCount'] > 200
        assert server.ground_truth
        assert all(len(item['Id']) > 20 and 'SIM-00001' != item['Id'] for item in first['Items'])
        wire = json.dumps(first, ensure_ascii=False).casefold()
        assert not any(key in wire for key in ['ground_truth', 'is_relevant', 'requirements_complete', 'completeness_variant'])
        keys = list(server.purchases)
    with selection_server() as (server, request):
        assert list(server.purchases) == keys
        assert request('POST', '/external/v1/search', search())[1] == first


def test_price_contract_documents_and_explicit_delivery_are_real_source_fields():
    from urllib.parse import urlsplit

    with selection_server() as (server, request):
        key = next(key for key, truth in server.ground_truth.items() if truth['all_complete'])
        status, card = request('GET', '/external/v1/purchases/' + key)
        assert status == 200
        lot = card['Lots'][0]
        assert set(lot['Price']) == {'Amount', 'RubPrice', 'CurrencyCode', 'Nds'}
        assert set(card['InitialSum']) == {'Price', 'Currency'}
        assert 'Поставка до ' in lot['Customers'][0]['EndContractDate']
        assert card['Docs'][0]['FileName'].endswith('.txt')
        path = urlsplit(card['Docs'][0]['Url']).path
        assert path == f'/documents/{key}.txt'
        assert request('GET', path, key='wrong-key')[0] == 401
        status, document = request('GET', path)
        assert status == 200
        for label in ['Поставка до:', 'Начало производства:', 'Срок доставки, дней:', 'Логистика, RUB:',
                      'Прочие обязательные расходы, RUB:', 'Налоговая база:', 'Данные о расходах подтверждены:']:
            assert label in document


@pytest.mark.parametrize('mode, expected', [('omitted', 'absent'), ('null', None), ('empty', []), ('masked', ['#####'])])
def test_snippets_are_optional_and_limit_modifier_matches_mode(mode, expected):
    with selection_server(snippets_mode=mode) as (server, request):
        items = request('POST', '/external/v1/search', search())[1]['Items']
        for item in items:
            assert ('Snippets' not in item) if expected == 'absent' else item['Snippets'] == expected
        limits = request('GET', '/external/v1/limitGroups')[1]
        if mode == 'omitted':
            assert 'Snippets' not in limits[0]['Modifiers']


def test_snippet_availability_does_not_change_seeded_corpus_or_labels():
    from kontur_simulator import selection_corpus

    today = datetime.fromisoformat(FIXED_DATE)
    mixed, mixed_truth = selection_corpus(today, 7319, 'mixed')
    absent, absent_truth = selection_corpus(today, 7319, 'omitted')
    assert list(mixed) == list(absent)
    assert mixed_truth == absent_truth
    for key in mixed:
        brief = mixed[key].brief(); brief.pop('Snippets', None)
        other = absent[key].brief(); other.pop('Snippets', None)
        assert brief == other


def test_same_visible_brief_can_have_different_mandatory_source_completeness():
    with selection_server() as (server, request):
        pair = [(key, truth) for key, truth in server.ground_truth.items() if truth.get('control_pair')]
        assert len(pair) == 2
        briefs = [server.purchases[key].brief() for key, _ in pair]
        for brief in briefs:
            for key in ['Id', 'NotificationNumber', 'Link']:
                brief.pop(key)
        assert briefs[0] == briefs[1]
        cards = [request('GET', '/external/v1/purchases/' + key)[1] for key, _ in pair]
        assert cards[0]['Lots'][0]['JointObjects'] != cards[1]['Lots'][0]['JointObjects']
        assert {truth['requirements'] for _, truth in pair} == {'complete', 'incomplete'}


def test_completeness_dimensions_and_relevance_are_independent():
    with selection_server() as (server, request):
        truth = list(server.ground_truth.values())
        assert {'product', 'neutral_product', 'document_product', 'service', 'mixed', 'unrelated', 'incompatible'} <= {row['kind'] for row in truth}
        for relevant in [True, False]:
            rows = [row for row in truth if row['relevant'] is relevant]
            for dimension in ['requirements', 'economics', 'schedule']:
                assert len({row[dimension] for row in rows}) > 1
        assert any(row['harmless_contact_mask'] and row['requirements'] == 'complete' for row in truth)


def test_pages_and_branch_duplicates_preserve_unique_ids():
    with selection_server() as (server, request):
        first = request('POST', '/external/v1/search', search())[1]
        second = request('POST', '/external/v1/search', search(page=1))[1]
        assert len(first['Items']) == len(second['Items']) == 50
        assert not {row['Id'] for row in first['Items']} & {row['Id'] for row in second['Items']}
        bolt = request('POST', '/external/v1/search', search('болт'))[1]
        broad_ids = {row['Id'] for row in first['Items'] + second['Items']}
        assert broad_ids & {row['Id'] for row in bolt['Items']}


def test_real_http_collection_deduplicates_branches_and_stays_within_budgets():
    from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient
    from bervel.modules.ai_tender_radar.selection import collect_candidates

    with selection_server() as (server, request):
        source = KonturClient(TEST_KEY, base_url=f'http://127.0.0.1:{server.server_port}', min_interval=0)
        checkpoint = {'search_start': search()['DateTimeFrom'], 'search_end': FIXED_DATE}
        result = collect_candidates(source, ['закупка', 'болт', 'закупка'], search_max=20,
                                    checkpoint=checkpoint, progress=lambda state: None)
        ids = [candidate['id'] for candidate in result['candidates']]
        assert len(ids) == len(set(ids)) == len(server.purchases)
        assert result['stop_reason'] == 'search_exhausted'
        assert result['search_attempts'] <= 20
        assert any(len(candidate['query_refs']) > 1 for candidate in result['candidates'])
        for key in ids[:200]:
            assert request('GET', '/external/v1/purchases/' + key)[0] == 200
        assert server.remaining['purchase'] == 0
        assert request('GET', '/external/v1/purchases/' + ids[200])[0] == 429


def test_http_source_checker_has_no_false_complete_on_controlled_cards():
    from bervel.modules.ai_tender_radar.completeness import check_completeness, extract_terms
    from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender

    with selection_server() as (server, request):
        confirmed = 0
        for key, truth in list(server.ground_truth.items())[:200]:
            status, card = request('GET', '/external/v1/purchases/' + key)
            assert status == 200
            tender = normalize_tender({'Id': key}, card)
            tender['terms'] = extract_terms(card)
            result = check_completeness(tender)
            assert not result['complete'] or truth['all_complete'], (key, truth, result)
            if truth['all_complete']:
                assert result['complete'], (key, result)
                confirmed += 1
        assert confirmed > 0
        assert server.remaining['purchase'] == 0


class BriefOnlyModel:
    """Deterministic protocol double, not evidence of a real LLM's quality."""
    def __init__(self):
        self.calls = 0

    def generate(self, request_id, prompt, **options):
        self.calls += 1
        inputs = json.loads(prompt.split('\nВХОД\n', 1)[1])
        entries = []
        for row in inputs['candidates']:
            text = row['title'] + ' ' + ' '.join(row['snippets'])
            matches = [product['id'] for product in inputs['products'] if product['name'].casefold() in text.casefold()]
            service = 'обслуживан' in row['title'].casefold() or 'монтаж' in row['title'].casefold()
            relevance = 'strong' if matches and not service else 'weak' if service else 'possible'
            entries.append({'id': row['id'], 'relevance': relevance, 'product_ids': matches,
                            'quote': row['title'] or row['snippets'][0]})
        return {'answer': json.dumps({'candidates': entries}, ensure_ascii=False), 'model': 'deterministic-protocol-double'}


def test_seeded_http_benchmark_records_honest_baseline_and_ranked_yield():
    from hashlib import sha256
    from pathlib import Path
    from bervel.modules.ai_tender_radar.infrastructure.kontur import KonturClient
    from bervel.modules.ai_tender_radar.selection import collect_candidates, rank_candidates, rerank_candidates
    from kontur_simulator import PRODUCTS

    profile = {'products': [{'id': f'p{index}', 'name': f'{name} {standard}', 'enabled': True}
                            for index, (name, standard, thread) in enumerate(PRODUCTS)], 'include_experimental': False}
    reports = []
    for seed in (7319, 19427):
        for strategy in ('api_order', 'cheap', 'hybrid_protocol_double'):
            with selection_server(seed=seed) as (server, request):
                source = KonturClient(TEST_KEY, base_url=f'http://127.0.0.1:{server.server_port}', min_interval=0)
                state = collect_candidates(source, ['закупка', 'болт'], search_max=20,
                    checkpoint={'search_start': search()['DateTimeFrom'], 'search_end': FIXED_DATE}, progress=lambda state: None)
                model = BriefOnlyModel()
                if strategy == 'api_order':
                    ordered = state['candidates'][:200]
                else:
                    ranked = rank_candidates(state['candidates'], profile)
                    if strategy == 'cheap':
                        ordered = ranked[:200]
                    else:
                        ordered, errors = rerank_candidates(ranked, profile, model, 'fixture-benchmark', detail_max=200, heartbeat=lambda: None)
                        assert errors == []
                assert len(ordered) == len({row['id'] for row in ordered}) == 200
                accepted = []
                for row in ordered:
                    assert request('GET', '/external/v1/purchases/' + row['id'])[0] == 200
                    truth = server.ground_truth[row['id']]
                    if truth['relevant'] and truth['all_complete']:
                        accepted.append(row['id'])
                total_eligible = sum(row['relevant'] and row['all_complete'] for row in server.ground_truth.values())
                reports.append({'seed': seed, 'strategy': strategy, 'candidates': len(state['candidates']),
                    'search_requests': state['search_attempts'], 'detail_requests': 200, 'model_calls': model.calls,
                    'eligible_in_corpus': total_eligible, 'yield': len(accepted), 'precision_at_200': len(accepted) / 200,
                    'recall_in_pool': len(accepted) / total_eligible, 'accepted_ids': accepted})
                assert server.remaining['purchase'] == 0
    # Measure the current selector honestly; fixture tests must not tune the corpus
    # until a particular selector wins. Acceptance gates are reported separately.
    gates = []
    for seed in (7319, 19427):
        results = {row['strategy']: row['yield'] for row in reports if row['seed'] == seed}
        gates.append({'seed': seed, 'cheap_improves_api': results['cheap'] > results['api_order'],
                      'hybrid_improves_api': results['hybrid_protocol_double'] > results['api_order'],
                      'hybrid_not_worse_than_cheap': results['hybrid_protocol_double'] >= results['cheap']})
    source_path = Path(__file__).parents[1] / 'scripts' / 'kontur_simulator.py'
    destination = Path(__file__).parents[1] / 'local' / 'ai-radar-selection-http' / 'benchmark.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({'source_sha256': sha256(source_path.read_bytes()).hexdigest(),
        'fixed_date': FIXED_DATE, 'detail_budget': 200,
        'interpretation': 'Synthetic fixture only. Protocol double is not a live LLM. Truth is read only after queues are fixed; completeness yield is oracle-labelled and does not validate the production checker.',
        'acceptance_gates': gates, 'runs': reports}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
