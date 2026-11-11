"""Cheap ordering and optimistic stock bounds, never a replacement for matching.

Only a proven upper bound below 80 can reject a tender. The frozen matcher only
admits fully evidenced SKU; the existing matcher/calculator own every result.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, localcontext
from fractions import Fraction
import re
from typing import Literal, TypedDict

from bervel.modules.ai_tender_radar.calculation import _scale, _unit
from bervel.modules.ai_tender_radar.completeness import check_completeness
from bervel.modules.ai_tender_radar.matching import (
    FAMILIES, Requirement, _catalog_item, _evidence, _family, _inline, _requirements, _text,
)
from bervel.modules.ai_tender_radar.models import StockData, TenderData
from bervel.modules.ai_tender_radar.source_contract import brief_is_closed
from bervel.modules.company_settings.models import active_experimental_products

LOT_COVERAGE_SELECTION_VERSION = 'catalog-prefilter-v5'
CANDIDATE_PRIORITY_VERSION = 'catalog-prefilter-v6'

_SUPPLY = re.compile(r'\b(?:поставк\w*|закупк\w*|приобретени\w*)\b')
_WORK = re.compile(r'\b(?:оказани\w* услуг|выполнени\w* работ|работы по|ремонт\b|техническ\w* обслуживани\w*)')
_GENERIC_FASTENERS = re.compile(r'\b(?:метиз\w*|креп[её]ж\w*)\b')
_FAMILY_MENTION = re.compile('|'.join(pattern.pattern for pattern in FAMILIES.values()))
EVIDENCE_PRIORITY_COUNT = 10


def prioritize_mentions(ranked: list[dict]) -> list[dict]:
    """Give the first requests stronger evidence, then favor breadth of catalog mentions."""
    prefix = [row for row in ranked if row['state'] != 'closed'][:EVIDENCE_PRIORITY_COUNT]
    selected = {row['id'] for row in prefix}
    tail = sorted((row for row in ranked if row['id'] not in selected), key=lambda row: (
        'SERVICE_CONTEXT' in row['reasons'], -row['rank_metadata']['product_mentions'], row['id']))
    return prefix + tail


class PrefilterDecision(TypedDict):
    state: Literal['reject', 'candidate', 'unknown']
    upper_coverage_percent: str | None
    reason: str


_GENERAL = {'поставка', 'поставку', 'поставки', 'закупка', 'закупку', 'товар', 'товаров',
            'изделия', 'изделий', 'для', 'на', 'и', 'по', 'в', 'с', 'ооо', 'ао', 'гост', 'din', 'iso'}


def _terms(value: str) -> set[str]:
    text = _text(value)
    terms = set(re.findall(r'\w+', text)) - _GENERAL
    # Use the same recognised product families as matching, including inflections.
    if family := _family(text):
        terms.add(family)
    return terms


def _quantity(value: str | None) -> Decimal | None:
    try:
        amount = Decimal(value) if value is not None else None
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount is not None and amount.is_finite() and amount >= 0 else None


def _decision(state, reason, upper=None) -> PrefilterDecision:
    value = format(upper, 'f') if upper is not None else None
    if value is not None and '.' in value:
        value = value.rstrip('0').rstrip('.')
    return {'state': state, 'upper_coverage_percent': value, 'reason': reason}


def _rounded(value: Fraction, rounding) -> Decimal:
    with localcontext() as context:
        context.prec = 100
        context.rounding = rounding
        return Decimal(value.numerator) / Decimal(value.denominator)


class CatalogPrefilter:
    def __init__(self, stock: StockData, profile: dict):
        # The caller supplies the same profile-projected stock as final matching.
        self.rows = {row['sku']: deepcopy(row) for row in stock['rows']}
        self.duplicate_skus = len(self.rows) != len(stock['rows'])
        self.catalog = [_catalog_item(row) for row in self.rows.values()]
        self.catalog_by_family = defaultdict(list)
        for item in self.catalog:
            families = set(item.families.values())
            if len(families) == 1:
                self.catalog_by_family[next(iter(families))].append(item)
        self.possibilities = {}
        self.products = [p for p in profile.get('products', []) if p.get('enabled')]
        official_count = len(self.products)
        self.products.extend(active_experimental_products(profile))
        self.product_terms = []
        self.index = defaultdict(list)
        self.stocked = set()
        self.families = set()
        self.other_products = defaultdict(list)
        for index, product in enumerate(self.products):
            terms = _terms(product['name'])
            self.product_terms.append(terms)
            for term in terms:
                self.index[term].append(index)
            name = _text(product['name'])
            if family := _family(name):
                self.families.add(family)
            elif terms:
                anchor = max(terms, key=lambda term: (len(term), term))
                self.other_products[anchor].append(name)
            row = self.rows.get(product.get('sku')) if index < official_count else None
            if row is not None and (amount := _quantity(row['free_quantity'])) is not None and amount > 0:
                self.stocked.add(index)

    def _hits(self, terms):
        counts = Counter(index for term in terms for index in self.index.get(term, ()))
        return {index: count / max(1, len(self.product_terms[index])) for index, count in counts.items()}

    def _mention_score(self, text: str) -> tuple[float, int, int]:
        # A kit is one product. Its parenthesized list describes its components.
        text = re.sub(r'(болто?комплект\w*)\s*\([^()]*\)', r'\1', _text(text))
        mentions = list(_FAMILY_MENTION.finditer(text))
        score = count = conflicts = 0
        for index, match in enumerate(mentions):
            family = _family(match[0])
            if family not in self.families:
                conflicts += 1
                continue
            count += 1
            end = mentions[index + 1].start() if index + 1 < len(mentions) else len(text)
            # Technical values are assigned to the preceding product, never pooled
            # across different products in a long snippet.
            facts, _ = _inline(text[match.end():end], family)
            requirements = [Requirement('family', 'family', '', family)]
            requirements.extend(Requirement(kind, kind, '', value) for kind, value in facts.items())
            if facts and self._possible(requirements):
                score += 3
            elif facts:
                score += .5
                conflicts += 1
            else:
                score += 1
        names = {name for term in _terms(text) for name in self.other_products.get(term, ())}
        # Prefer the longest product phrase when catalog names overlap.
        if names:
            alternatives = '|'.join(re.escape(name) for name in sorted(names, key=lambda n: (-len(n), n)))
            exact = len(re.findall(r'(?<!\w)(?:' + alternatives + r')(?!\w)', text))
            score += 3 * exact
            count += exact
        if not mentions and self.families and _GENERIC_FASTENERS.search(text):
            score += .75
        return score, count, conflicts

    def rank(self, candidates: list[dict], *, strategy: str | None = None,
             purchase_stages: list[str] | None = None) -> list[dict]:
        """Order all briefs. Lexical hints are not suitability or coverage claims."""
        result = []
        for original in candidates:
            row = deepcopy(original)
            item = row.get('brief') or {}
            title = str(item.get('OrderName') or '')
            snippets = ' '.join(s for s in (item.get('Snippets') or []) if isinstance(s, str))
            title_hits, snippet_hits = self._hits(_terms(title)), self._hits(_terms(snippets))
            related = title_hits.keys() | snippet_hits.keys()
            scores = {index: 70 * title_hits.get(index, 0) + 30 * snippet_hits.get(index, 0)
                      for index in related}
            strongest = sorted(scores, key=lambda index: (-scores[index], index))
            service = bool(re.search(r'обслуживан|оказание услуг|ремонт', title, re.I))
            row['rank'] = max(scores.values(), default=0) - (35 if service else 0)
            row['uncertainty'] = not title or not snippets or max(title_hits.values(), default=0) < .5
            row['reasons'] = ['SERVICE_CONTEXT'] if service else []
            if strongest:
                row['reasons'].append('CATALOG_LEXICAL_HINT')
            row['rank_metadata'] = {'kind': 'lexical_hint',
                'product_ids': [self.products[index]['id'] for index in strongest[:8]],
                'stock_available_hint': any(index in self.stocked for index in strongest[:8])}
            if strategy == CANDIDATE_PRIORITY_VERSION:
                headline, count, conflicts = self._mention_score(title)
                fragments = dict.fromkeys(_text(s) for s in item.get('Snippets') or [] if isinstance(s, str))
                fragments.pop(_text(title), None)
                detail_score = 0
                for fragment in fragments:
                    strength, occurrences, mismatches = self._mention_score(fragment)
                    detail_score += strength
                    count += occurrences
                    conflicts += mismatches
                supply, work = _SUPPLY.search(_text(title)), _WORK.search(_text(title))
                service = bool(work and (not supply or work.start() < supply.start()))
                score = 2 * headline + detail_score - 2 * conflicts
                row['rank'] = score - max(2, .8 * score) if service else score
                row['uncertainty'] = count == 0 or not any(fragments) or conflicts > 0
                row['reasons'] = (['SERVICE_CONTEXT'] if service else []) + (
                    ['CATALOG_PRODUCT_MENTIONS'] if count else []) + (['VISIBLE_CATALOG_CONFLICT'] if conflicts else [])
                row['rank_metadata'].update(kind='product_mentions', product_mentions=count,
                                            visible_conflicts=conflicts)
            if brief_is_closed(item, purchase_stages):
                row['state'] = 'closed'
                row['reasons'].append('CLOSED_PROCEDURE')
            result.append(row)
        return sorted(result, key=lambda row: (-row['rank'],
            -int(strategy != CANDIDATE_PRIORITY_VERSION and row['rank_metadata']['stock_available_hint']), row['id']))

    def _possible(self, requirements):
        key = tuple((req.kind, req.value) for req in requirements)
        if key not in self.possibilities:
            possible = []
            family = next((req.value for req in requirements if req.kind == 'family'), None)
            items = self.catalog if family is None else self.catalog_by_family.get(_text(family), ())
            for item in items:
                evidence = [_evidence(req, item) for req in requirements]
                # _validate_batch cannot accept review_candidates or missing
                # proofs. Keeping every fully evidenced row (without its limit
                # of 12) is a superset of every valid final model decision.
                if all(proof and not conflict for proof, conflict in evidence):
                    possible.append(item.sku)
            self.possibilities[key] = tuple(possible)
        return self.possibilities[key]

    def catalog_match(self, position) -> bool | None:
        requirements = _requirements(position)
        return None if requirements is None else bool(self._possible(requirements))

    def check(self, tender: TenderData) -> PrefilterDecision:
        if not tender.get('positions'):
            return _decision('unknown', 'TENDER_INVENTORY_UNCONFIRMED')
        # documents._inventory forbids replacing structured positions. Unread
        # documents may only add restrictions, which cannot increase this bound.
        document_limits = {'DOCUMENT_EXTRACTION_UNKNOWN', 'DOCUMENT_REQUIREMENTS_INCOMPLETE'}
        if any(issue['code'] not in document_limits for issue in tender.get('limitations') or []):
            return _decision('unknown', 'TENDER_REQUIREMENTS_INCOMPLETE')
        documents_pending = check_completeness(tender)['requirements'] != 'complete'
        if self.duplicate_skus:
            return _decision('unknown', 'STOCK_SKU_AMBIGUOUS')
        prepared = []
        for position in tender['positions']:
            quantity = _quantity(position.get('quantity'))
            unit = _unit(position.get('unit'))
            if quantity is None or quantity <= 0 or unit is None:
                return _decision('unknown', 'PIECE_QUANTITY_UNCONFIRMED')
            requirements = _requirements(position)
            if requirements is None:
                return _decision('unknown', 'POSITION_REQUIREMENTS_INCOMPLETE')
            prepared.append((quantity, unit, self._possible(requirements)))
        # Relevance has an existential proof independent of units: no accepted
        # SKU for any known row means the final validator cannot confirm supply.
        # A single proven line prevents this gate, even in a mixed inventory.
        if not any(skus for _, _, skus in prepared):
            return _decision('reject', 'NO_PROVABLE_CATALOG_MATCH', Decimal(0))
        # Bounds use continuous equivalent pieces, relaxing integer allocation.
        # Exact fractions feed directed Decimal intervals. The frozen calculator
        # rounds repeating mass conversions at precision100; its numerical result
        # can be slightly above the exact ratio, so the denominator rounds down.
        with localcontext() as context:
            context.prec = 100
            context.rounding = ROUND_CEILING
            demand_total = independent_upper = Decimal(0)
            supplies = {}
            mass_positions = False
            for quantity, unit, skus in prepared:
                # The model may omit any candidate. A common proven scale is
                # therefore required across every possible numeric final result.
                scales = {_scale(unit, self.rows[sku]) for sku in skus}
                if unit == 'шт':
                    scale = (Decimal(1), Decimal(1))
                else:
                    if unit == 'кг':
                        scales.discard(None)  # same rule as frozen calculator
                    if len(scales) != 1 or None in scales:
                        return _decision('unknown', 'POSITION_UNIT_CONVERSION_UNCONFIRMED')
                    scale = next(iter(scales))
                demand = Fraction(quantity) * Fraction(scale[0]) / Fraction(scale[1])
                demand_lower = _rounded(demand, ROUND_FLOOR)
                demand_upper = _rounded(demand, ROUND_CEILING)
                supply = Decimal(0)
                for sku in skus:
                    row = self.rows[sku]
                    amount = _quantity(row['free_quantity'])
                    if amount is None:
                        return _decision('unknown', 'STOCK_QUANTITY_UNCONFIRMED')
                    if amount == 0:
                        continue
                    stock_scale = _scale(_unit(row['unit']), row)
                    if stock_scale is None:
                        return _decision('unknown', 'STOCK_UNIT_CONVERSION_UNCONFIRMED')
                    pieces = _rounded(Fraction(amount) * Fraction(stock_scale[0]) / Fraction(stock_scale[1]),
                                      ROUND_CEILING)
                    supply += pieces
                    supplies[sku] = pieces
                if unit == 'кг':
                    mass_positions = True
                    # A mixed-unit allocation is converted to native mass and
                    # back by the calculator. Enclose both rounded operations.
                    supply = (supply * scale[1] / scale[0]) * scale[0] / scale[1]
                with localcontext() as lower_context:
                    lower_context.rounding = ROUND_FLOOR
                    demand_total += demand_lower
                independent_upper += min(demand_upper, supply)
            # Both bounds overestimate every feasible allocation. The union cap
            # prevents counting a shared stock row twice without allocating it.
            # With mass positions, per-line rounded native conversions can each
            # add an ulp. Keep the independent upper bound rather than claiming
            # an exact shared-stock cap across those rounded conversions.
            upper_pieces = independent_upper if mass_positions else min(
                independent_upper, sum(supplies.values(), Decimal(0)))
            upper = 100 * upper_pieces / demand_total
            if upper < 80:
                return _decision('reject', 'STOCK_UPPER_BOUND_BELOW_80', upper)
            if documents_pending:
                return _decision('unknown', 'DOCUMENT_REQUIREMENTS_PENDING', upper)
            return _decision('candidate', 'STOCK_UPPER_BOUND_REQUIRES_ANALYSIS', upper)
