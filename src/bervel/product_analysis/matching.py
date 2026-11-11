"""Pure, evidence-based technical product matching."""
from dataclasses import dataclass
from decimal import Decimal
import re
from unicodedata import normalize
from .models import Demand, StockRow, CandidateSelection, PreparedCandidates, MatchResult

MAX_CANDIDATES = 12
MAX_REVIEW_CANDIDATES = 4
_FAMILIES = {
    'болтокомплект': r'болто?комплект(?:ы|а|ов|у|ом|ами|ах|ам|е)?',
    'болт': r'болт(?:ы|а|ов|у|ом|ами|ах|ам|е)?',
    'гайка': r'гайк(?:а|и|у|ой|ам|ами|ах)|гаек',
    'шайба': r'шайб(?:а|ы|у|ой|ам|ами|ах)?',
    'шпилька': r'шпильк(?:а|и|у|ой|ам|ами|ах)|шпилек',
    'винт': r'винт(?:ы|а|ов|у|ом|ами|ах|ам|е)?',
}
FAMILIES = {key: re.compile(rf'(?<!\w)(?:{pattern})(?!\w)') for key, pattern in _FAMILIES.items()}
THREAD = re.compile(r'(?<!\w)[мm]\s*(\d+(?:[.,]\d+)?)(?![\d.,])')
PLAIN_THREAD = re.compile(THREAD.pattern + r'(?!\s*[xх×*]\s*\d)')
BOLT_SIZE = re.compile(THREAD.pattern + r'\s*[xх×*]\s*(\d+(?:[.,]\d+)?)(?:\s*[xх×*]\s*(\d+(?:[.,]\d+)?))?(?![\d.,])')
NUMERIC = re.compile(r'([0-9]+(?:[.,][0-9]+)?)(?:\s*мм)?')
STANDARD = re.compile(r'(гост(?: р)?|сто|дин|din|iso)\s*([0-9]+)(?:\s*-\s*([0-9]+))?')
STANDARD_TOKEN = re.compile(rf'(?<!\w)(?:{STANDARD.pattern})(?![\w-])')
STRENGTH_TOKEN = re.compile(r'(?<!\w)(?:класс(?:\s+прочности)?|прочность|к\.\s*п\.?)\s*([0-9]+[.,][0-9]+)(?![\d.,])')
INLINE_LABELS = {'thread_diameter_mm': 'Диаметр резьбы', 'length_mm': 'Длина', 'thread_pitch_mm': 'Шаг резьбы',
                 'standard': 'Стандарт', 'strength': 'Класс прочности', 'nominal_diameter_mm': 'Номинал шайбы'}
ALIASES = {
    'thread_diameter_mm': ('диаметр резьбы', 'номинальный диаметр резьбы', 'размер резьбы', 'резьба'),
    'diameter_mm': ('диаметр', 'номинальный диаметр'),
    'length_mm': ('длина', 'длина болта', 'длина изделия'),
    'thread_pitch_mm': ('шаг резьбы', 'шаг'),
    'inner_diameter_mm': ('внутренний диаметр',),
    'outer_diameter_mm': ('наружный диаметр', 'внешний диаметр'),
    'height_mm': ('высота',),
    'thickness_mm': ('толщина',),
    'strength': ('класс прочности', 'прочность'),
    'accuracy_class': ('класс точности',),
    'shape': ('форма',),
    'standard': ('гост', 'стандарт', 'нормативный документ'),
    'coating': ('покрытие', 'вид покрытия', 'тип покрытия'),
    'material': ('материал', 'материал изделия'),
    'material_grade': ('марка стали', 'марка материала'),
    'head_type': ('тип головки', 'форма головки'),
}
FIELD_FOR_NAME = {alias: field for field, aliases in ALIASES.items() for alias in (field, *aliases)}
CHOOSE = 'участник закупки указывает в заявке конкретное значение характеристики'


@dataclass(frozen=True)
class Requirement:
    id: str
    kind: str
    label: str
    value: str


@dataclass
class CatalogItem:
    sku: str
    fields: dict[str, str]
    families: dict[str, str]
    inline: dict[str, str]
    has_stock: bool


def _text(value: str) -> str:
    return ' '.join(normalize('NFKC', value).casefold().replace('ё', 'е').strip(' .').split())


def _family(value: str) -> str | None:
    return next((family for family, pattern in FAMILIES.items() if pattern.search(value)), None)


def _number(value: str) -> str | None:
    match = NUMERIC.fullmatch(value)
    if not match:
        return None
    integer, _, fraction = match[1].replace(',', '.').partition('.')
    fraction = fraction.rstrip('0')
    return (integer.lstrip('0') or '0') + ('.' + fraction if fraction else '')


def _inline(value: str, family: str | None) -> tuple[dict[str, str], str]:
    facts = {}
    thread = THREAD.search(value)
    if thread:
        facts['thread_diameter_mm'] = thread[1]
        size = BOLT_SIZE.match(value, thread.start()) if family in {'болт', 'шпилька', 'винт'} else None
        if size and (size[3] is not None or size[2].isdigit() and len(size[2]) < 6
                     and int(size[2]) >= 5 and Decimal(size[2]) > Decimal(thread[1].replace(',', '.'))):
            facts['length_mm'] = size[3] or size[2]
            if size[3] is not None:
                facts['thread_pitch_mm'] = size[2]
            value = value[:size.start()] + ' ' + value[size.end():]
        elif PLAIN_THREAD.match(value, thread.start()):
            value = value[:thread.start()] + ' ' + value[thread.end():]
    standards = list(STANDARD_TOKEN.finditer(value))
    if len(standards) == 1:
        standard = standards[0]
        facts['standard'] = standard[0]
        value = value[:standard.start()] + ' ' + value[standard.end():]
    if family == 'шайба' and not facts.get('thread_diameter_mm'):
        if nominal := _number(value.strip()):
            facts['nominal_diameter_mm'] = nominal
            value = ''
    strength = STRENGTH_TOKEN.search(value)
    if strength:
        facts['strength'] = strength[1]
        value = value[:strength.start()] + ' ' + value[strength.end():]
    return facts, value.strip(' ,.;()')


def _requirements(position: Demand) -> list[Requirement] | None:
    if not position['name']:
        return None
    name = re.sub(r'^~\s*(?=[а-яa-z])', '', _text(position['name']))
    family = _family(name)
    requirements = []
    rest = name
    if family:
        requirements.append(Requirement('family', 'family', 'Вид изделия', family))
        rest = FAMILIES[family].sub('', rest, count=1).strip()
        inline, rest = _inline(rest, family)
        requirements.extend(Requirement(field, field, INLINE_LABELS[field], value) for field, value in inline.items())
    if rest:
        requirements.append(Requirement('name', 'name', 'Наименование или артикул', position['name']))
    for index, feature in enumerate(position['characteristics']):
        values = {(value['Value'], value['Okei']) for value in feature['Values'] if value['Value']}
        if not values and _text(feature['FillingInstruction'] or '') == CHOOSE:
            continue
        if not feature['Name'] or len(values) != 1:
            return None
        value, unit = next(iter(values))
        field = FIELD_FOR_NAME.get(_text(feature['Name']), _text(feature['Name']))
        if unit and (not field.endswith('_mm') or _text(unit) not in {'мм', 'миллиметр', 'миллиметры', 'mm', '003'}):
            return None
        requirements.append(Requirement(f'characteristic-{index}', field, feature['Name'], value))
    return requirements if any(requirement.kind != 'family' for requirement in requirements) else None


def _stock_fields(row: StockRow) -> dict[str, str]:
    fields = {'product': row['product'], 'sku': row['sku']}
    for key, value in row['attributes'].items():
        if not (key.endswith(('_options', '_price', '_cost')) or key.startswith(('source_', 'mock_', 'price_', 'cost_'))
                or key in {'price', 'cost', 'technical_parameters'}):
            fields[key] = value
    return fields


def _catalog_item(row: StockRow) -> CatalogItem:
    fields = _stock_fields(row)
    families = {key: family for key in ('product', 'product_subtype', 'product_group')
                if key in fields and (family := _family(_text(fields[key])))}
    inline, _ = _inline(_text(fields['product']), families.get('product'))
    return CatalogItem(row['sku'], fields, families, inline, row['free_quantity'] is None or _number(row['free_quantity']) != '0')


def _standard(required: str, actual: str) -> bool:
    expected, found = STANDARD.fullmatch(required), STANDARD.fullmatch(actual)
    if not expected or not found:
        return required == actual
    return expected.group(1, 2) == found.group(1, 2) and (expected[3] is None or expected[3] == found[3])


def _evidence(requirement: Requirement, item: CatalogItem) -> tuple[dict[str, str], bool]:
    """Return source fields that prove the requirement and whether a conflict is known."""
    kind, required = requirement.kind, _text(requirement.value)
    fields = item.fields
    if kind == 'family':
        observed = item.families
        positive = {key: fields[key] for key, value in observed.items() if value == required}
        return positive if len(positive) == len(observed) else {}, bool(observed) and not positive
    if kind == 'name':
        candidates = ('product', 'sku', 'manufacturer_part_number', 'original_name')
        return {key: fields[key] for key in candidates if key in fields and _text(fields[key]) == required}, False

    keys = (kind, *ALIASES.get(kind, ()))
    observations = {key: _text(fields[key]) for key in keys if key in fields}
    if kind in item.inline:
        observations['product'] = item.inline[kind]
    if kind == 'thread_diameter_mm' and (thread := THREAD.search(_text(fields.get('thread', '')))):
        observations['thread'] = thread[1]
    if kind == 'shape' and item.families.get('product') == 'гайка' and 'product_subtype' in fields:
        observations['product_subtype'] = _text(fields['product_subtype'])
    if kind.endswith('_mm') or kind == 'strength':
        if kind == 'thread_diameter_mm' and (thread := PLAIN_THREAD.fullmatch(required)):
            required = thread[1]
        expected = _number(required)
        checks = {}
        for key, value in observations.items():
            if kind == 'thread_diameter_mm' and (thread := PLAIN_THREAD.fullmatch(value)):
                value = thread[1]
            actual = _number(value)
            if expected is not None and actual is not None:
                checks[key] = expected == actual
    elif kind == 'standard':
        checks = {key: _standard(required, value) for key, value in observations.items()}
    elif kind == 'accuracy_class':
        classes = {'а': 'a', 'в': 'b', 'с': 'c'}
        checks = {key: classes.get(required, required) == classes.get(value, value)
                  for key, value in observations.items()}
    else:
        checks = {key: required == value for key, value in observations.items()}
    positive = {key: fields[key] for key, matched in checks.items() if matched}
    return positive if len(positive) == len(checks) else {}, bool(checks) and not positive


def _prepare(position: Demand, catalog: list[CatalogItem]) -> tuple[dict | None, bool]:
    requirements = _requirements(position)
    if requirements is None:
        return None, True
    candidates = []
    review_candidates = []
    uncertain = False
    for item in catalog:
        evidence = {}
        for requirement in requirements:
            proof, conflict = _evidence(requirement, item)
            if conflict:
                break
            if proof:
                evidence[requirement.id] = proof
        else:
            if len(evidence) == len(requirements) and len(candidates) < MAX_CANDIDATES:
                candidates.append({'sku': item.sku, 'product': item.fields['product'], 'evidence': evidence})
            elif evidence:
                uncertain = True
                if evidence and len(evidence) < len(requirements):
                    review_candidates.append({'sku': item.sku, 'product': item.fields['product'], 'evidence': evidence,
                        'unconfirmed_requirements': [req.id for req in requirements if req.id not in evidence]})
                    review_candidates.sort(key=lambda candidate: -len(candidate['evidence']))
                    del review_candidates[MAX_REVIEW_CANDIDATES:]
    return {'id': position['id'], 'name': position['name'],
            'requirements': {req.id: {'name': req.label, 'value': req.value} for req in requirements},
            'candidates': candidates, 'review_candidates': review_candidates}, uncertain


def _saved_evidence(requirements: list[Requirement], item: CatalogItem, evidence: object) -> bool:
    if not isinstance(evidence, list) or len(evidence) != len(requirements):
        return False
    remaining = list(evidence)
    for requirement in requirements:
        fields, _ = _evidence(requirement, item)
        reference = next((index for index, fact in enumerate(remaining) if isinstance(fact, dict)
                          and fact.get('requirement') == requirement.label
                          and fact.get('tender_value') == requirement.value
                          and fact.get('stock_field') in fields
                          and fact.get('stock_value') == fields[fact['stock_field']]), None)
        if reference is None:
            return False
        remaining.pop(reference)
    return True


def prepare_candidates(demand: Demand, rows) -> PreparedCandidates:
    """Inspect every row; model batch limits never truncate the product catalog."""
    requirements = _requirements(demand)
    if requirements is None:
        return PreparedCandidates({}, [], [], True)
    candidates, review = [], []
    uncertain = False
    for row in rows:
        prepared, row_uncertain = _prepare(demand, [_catalog_item(row)])
        candidates.extend(prepared['candidates'])
        review.extend(prepared['review_candidates'])
        uncertain |= row_uncertain
    return PreparedCandidates({r.id: {'name': r.label, 'value': r.value} for r in requirements},
                              candidates, review, uncertain)


def validate_selected_evidence(demand: Demand, rows, selections) -> MatchResult:
    """Accept only facts recomputed from the original catalog, never model assertions."""
    prepared = prepare_candidates(demand, rows)
    available = {item['sku']: item for item in prepared.candidates}
    selected = {}
    invalid = False
    for selection in selections:
        sku = selection.get('sku')
        candidate = available.get(sku)
        supplied = selection.get('evidence')
        if candidate is None or sku in selected or not isinstance(supplied, dict) or set(supplied) != set(prepared.requirements):
            invalid = True
            continue
        facts = []
        for requirement_id, options in candidate['evidence'].items():
            proof = supplied[requirement_id]
            if not isinstance(proof, dict) or not proof or any(options.get(k) != v for k, v in proof.items()):
                invalid = True
                break
            field = next(iter(proof))
            requirement = prepared.requirements[requirement_id]
            facts.append({'requirement': requirement['name'], 'tender_value': requirement['value'],
                          'stock_field': field, 'stock_value': proof[field]})
        else:
            selected[sku] = facts
    uncertain = invalid or not prepared.requirements or (not selected and prepared.uncertain)
    return MatchResult(selected, uncertain, 'EVIDENCE_UNCONFIRMED' if uncertain else None)


def inspect_candidate(demand: Demand, row: StockRow) -> dict:
    """Describe all proven, conflicting and missing requirements for manager review."""
    item = _catalog_item(row)
    requirements = _requirements(demand)
    evidence, differences = [], []
    for requirement in requirements or []:
        proof, conflict = _evidence(requirement, item)
        if proof:
            field = next(iter(proof))
            evidence.append(dict(requirement_id=requirement.id, requirement=requirement.label,
                                 required_value=requirement.value, field=field, value=proof[field]))
        else:
            differences.append(dict(requirement_id=requirement.id, name=requirement.label,
                                    required_value=requirement.value, conflict=conflict,
                                    actual_fields=item.fields.copy()))
    return dict(sku=row['sku'], name=row['product'], evidence=evidence, differences=differences,
                fields=item.fields.copy(), exact=bool(requirements) and not differences,
                related=any(fact['requirement_id'] == 'family' for fact in evidence))


def catalog_characteristics(row: StockRow) -> list[dict[str, str]]:
    """Describe the offered SKU itself, including dimensions parsed from its name."""
    item = _catalog_item(row)
    facts = []
    for field, value in item.fields.items():
        if field in {'product','sku'}:
            continue
        kind = FIELD_FOR_NAME.get(_text(field),field)
        label = INLINE_LABELS.get(kind) or (ALIASES[kind][0].capitalize() if kind in ALIASES else field)
        fact = dict(name=label,value=value)
        if fact not in facts:
            facts.append(fact)
    for kind,value in item.inline.items():
        label = INLINE_LABELS[kind]
        fact = dict(name=label,value=value)
        if fact not in facts:
            if any(existing['name']==label for existing in facts):
                fact['name'] += ' (наименование SKU)'
            facts.append(fact)
    return facts


