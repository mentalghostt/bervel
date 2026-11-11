import json

import pytest

from bervel.core.errors import CoreError


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    monkeypatch.setattr('bervel.modules.ai_tender_radar.brief_ranking._wait_for_retry',
                        lambda delay, heartbeat: heartbeat())


def brief(identifier, title, *, snippets=None, state='candidate'):
    return {'id': identifier, 'state': state,
            'brief': {'Id': identifier, 'OrderName': title, 'Snippets': snippets}}


def product(identifier, name, *, enabled=True):
    return {'id': identifier, 'name': name, 'enabled': enabled, 'attributes': {}}


class RankingModel:
    def __init__(self):
        self.payloads = []

    @property
    def batches(self):
        return [payload['candidates'] for payload in self.payloads]

    def generate(self, request_id, prompt, *, output_schema, web_search, timeout_seconds):
        assert web_search is False
        assert output_schema is None
        payload = json.loads(prompt.partition('\nВХОД\n')[2])
        batch = payload['candidates']
        self.payloads.append(payload)
        assert len(batch) == 1
        return {'answer': '96' if batch[0]['id'] == 'special' else '25', 'model': 'test'}


def rank(candidates, model, profile=None):
    from bervel.modules.ai_tender_radar.brief_ranking import rank_briefs_with_model
    return rank_briefs_with_model(candidates, profile if profile is not None else {
        'products': [product('bolt', 'Болт М12')],
    }, model, 'ranking', heartbeat=lambda: None)


def test_model_scores_every_unique_candidate_even_with_a_large_catalog():
    products = [product(str(i), f'Болт М12×{i + 5}') for i in range(3075)]
    products += [product('rare', 'Кольцо упорное КУ88'), product('inactive', 'КУ88', enabled=False)]
    candidates = [brief(str(i), 'Поставка комплектующих') for i in range(70)]
    candidates += [brief('special', 'Поставка кольца упорного КУ88'),
                   brief('closed', 'Поставка болтов М12', state='closed'), candidates[0]]
    model = RankingModel()

    ranked = rank(candidates, model, {'products': products})

    assert len(model.batches) > 1
    submitted = [row for batch in model.batches for row in batch]
    assert len(submitted) == 71
    assert {row['id'] for row in submitted} == {str(i) for i in range(70)} | {'special'}
    assert ranked[0]['id'] == 'special'
    assert ranked[0]['rank'] == 96
    assert ranked[0]['model_evidence'] == {'score': 96}
    special = next(payload for payload in model.payloads if payload['candidates'][0]['id'] == 'special')
    assert 'rare' in {p['id'] for p in special['catalog_products']}
    assert ranked[-1]['id'] == 'closed'
    assert not any(p['id'] == 'inactive' for row in submitted for p in row['products'])
    assert 'rank' not in candidates[0]


def test_model_failure_is_not_replaced_with_lexical_ranking():
    class UnavailableModel:
        def generate(self, *args, **kwargs):
            raise CoreError('MODEL_UNAVAILABLE', 'Модель недоступна.')

    with pytest.raises(CoreError) as error:
        rank([brief('a', 'Болты М12')], UnavailableModel())
    assert error.value.code == 'MODEL_UNAVAILABLE'


def test_empty_brief_still_receives_a_numeric_priority():
    ranked = rank([brief('a', None, snippets=[None, ' '])], RankingModel())
    assert ranked[0]['model_evidence'] == {'score': 25}


def test_each_tender_receives_only_its_bounded_related_product_context():
    model = RankingModel()
    ranked = rank([brief('a', 'Болты М12'), brief('b', 'Поставка болтов М12')], model)
    assert len(ranked) == 2
    assert len(model.payloads) == 2
    for payload in model.payloads:
        assert [(entry['id'], entry['name']) for entry in payload['catalog_products']] == [('bolt', 'Болт М12')]
        assert payload['candidates'][0]['products'] == [{'id': 'bolt'}]
