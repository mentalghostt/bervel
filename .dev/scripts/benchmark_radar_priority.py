"""Compare queues offline. Full cards are opened only by the evaluator."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path

from bervel.modules.ai_tender_radar.infrastructure.kontur import normalize_tender
from bervel.modules.ai_tender_radar.infrastructure.stock import read_stock
from bervel.modules.ai_tender_radar.matching import STANDARD_TOKEN, _family, _text
from bervel.modules.ai_tender_radar.prefilter import CANDIDATE_PRIORITY_VERSION, CatalogPrefilter, prioritize_mentions
from bervel.modules.ai_tender_radar.result_metrics import lot_matches
from bervel.modules.ai_tender_radar.selection import collect_candidates, search_queries_v3
from kontur_simulator import Simulator


class Replay:
    simulated = True

    def __init__(self, source):
        self.source = source
        self.search_page = lru_cache(maxsize=1024)(self.search_page)

    def search_page(self, phrase, page, start, end):
        return self.source.search({'Text': [phrase], 'PageNumber': page, 'DateTimeFrom': start,
                                   'DateTimeTo': end, 'PurchaseStatuses': [1], 'Attachments': True,
                                   'MaxPriceNone': True, 'AllowForeignCurrency': True})


def queries(profile, precise):
    broad = search_queries_v3(profile)
    if not precise:
        return broad
    exact = Counter()
    for product in profile['products']:
        attributes = product['attributes']
        group = attributes.get('family') or attributes.get('group') or attributes.get('product_group')
        if family := _family(_text(group or product['name'])):
            standards = _text(product['attributes'].get('standard') or product['name'])
            exact.update(f'{family} {token[0]}' for token in STANDARD_TOKEN.finditer(standards))
    return list(dict.fromkeys([*broad, *sorted(exact, key=lambda phrase: (-exact[phrase], phrase))]))


def compare(stock_path, seed, negative_count, corpus, limits, *, broad_only=False):
    stock = read_stock(stock_path.read_bytes())
    profile = {'products': [dict(id=r['sku'], sku=r['sku'], enabled=True, name=r['product'],
                                attributes=r['attributes']) for r in stock['rows']]}
    engine = CatalogPrefilter(stock, profile)
    # Future deadlines keep this fixture independent of the machine clock.
    today = datetime(2090, 11, 6, tzinfo=timezone.utc)
    with Simulator(0, profile=corpus, seed=seed, today=today, stock_csv=stock_path,
                   positive_count=30, negative_count=negative_count) as source:
        evaluator = {}
        replay = Replay(source)

        def evaluate(candidate):
            key = candidate['id']
            if key not in evaluator:
                tender = normalize_tender(candidate['brief'], source.purchases[key].card())
                lots = lot_matches(tender, engine.catalog_match)
                evaluator[key] = (sum(v is True for v in lots.values()), bool(lots) and all(v is True for v in lots.values()))
            return evaluator[key]

        for detail_max in limits:
            plans = [(detail_max, False)]
            if not broad_only:
                plans.append((min(200, 2 * detail_max), True))
            for search_max, precise in plans:
                state = collect_candidates(replay, queries(profile, precise),
                    search_max=search_max, checkpoint={'search_start': '2089-01-01T00:00:00Z',
                    'search_end': today.isoformat()}, progress=lambda _: None)
                pool = state['candidates']
                old = engine.rank(pool)
                new = engine.rank(pool, strategy=CANDIDATE_PRIORITY_VERSION)
                mentions = sorted(new, key=lambda row: ('SERVICE_CONTEXT' in row['reasons'],
                                  -row['rank_metadata']['product_mentions'], row['id']))
                old_ranks = {row['id']: row['rank'] for row in old}
                hybrid = sorted(new, key=lambda row: (-(row['rank'] + max(0, old_ranks[row['id']]) / 10), row['id']))
                count_then_evidence = sorted(new, key=lambda row: ('SERVICE_CONTEXT' in row['reasons'],
                    -row['rank_metadata']['product_mentions'], -row['rank'], row['id']))
                layered = prioritize_mentions(new)
                adaptive = []
                history = {}
                pending = [row for row in new if row['state'] != 'closed']
                # Adjust nearby candidates only, based on the matching lots in
                # batches already opened by this policy.
                def bucket(row):
                    return min(6, row['rank_metadata']['product_mentions']), row['uncertainty']

                def priority(row):
                    total, observations = history.get(bucket(row), (0, 0))
                    return row['rank'] + (total - 2 * observations) / (8 + observations)

                while pending and len(adaptive) < detail_max:
                    pending.sort(key=lambda row: (-priority(row), row['id']))
                    size = min(4, detail_max - len(adaptive))
                    batch, pending = pending[:size], pending[size:]
                    for row in batch:
                        total, observations = history.get(bucket(row), (0, 0))
                        history[bucket(row)] = total + evaluate(row)[0], observations + 1
                    adaptive.extend(batch)
                for label, queue in (('old', old), ('mentions_only', mentions), ('new', new),
                                     ('hybrid', hybrid), ('count_then_evidence', count_then_evidence),
                                     ('layered', layered), ('adaptive', adaptive)):
                    selected = [row for row in queue if row['state'] != 'closed'][:detail_max]
                    outcomes = [evaluate(row) for row in selected]
                    print(json.dumps({'seed': seed, 'corpus': corpus, 'strategy': label, 'precise': precise,
                                      'search': state['search_attempts'], 'pool': len(pool), 'detail': len(outcomes),
                                      'matched_lots': sum(n for n, _ in outcomes),
                                      'full_tenders': sum(full for _, full in outcomes),
                                      'matched_tenders': sum(n > 0 for n, _ in outcomes)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stock', type=Path)
    parser.add_argument('--seeds', type=int, nargs='+', default=[71993, 82153, 91639])
    parser.add_argument('--negatives', type=int, default=4970)
    parser.add_argument('--limits', type=int, nargs='+', default=[10, 50, 200])
    parser.add_argument('--broad-only', action='store_true')
    parser.add_argument('--corpus', choices=('realistic', 'coverage'), default='realistic')
    args = parser.parse_args()
    for seed in args.seeds:
        compare(args.stock, seed, args.negatives, args.corpus, args.limits, broad_only=args.broad_only)
