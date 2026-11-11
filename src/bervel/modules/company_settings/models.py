"""Company facts and catalog rules, independent of HTTP and persistence."""
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import json
import re
from typing import Any, NotRequired, TypedDict
from uuid import UUID

from bervel.core.errors import CoreError



class Product(TypedDict):
    id: str
    sku: str | None
    name: str
    unit: str
    attributes: dict[str, str]
    enabled: bool
    manufactured_before: NotRequired[bool | None]
    evidence: NotRequired[str]


class ExperimentalProduct(TypedDict):
    id: str
    name: str
    unit: str
    attributes: dict[str, str]
    enabled: bool
    sku: NotRequired[str | None]
    product_id: NotRequired[str | None]


class Profile(TypedDict):
    stock_artifact_id: str
    stock_sha256: str
    products: list[Product]
    experimental_products: list[ExperimentalProduct]
    include_experimental: bool
    production: list[dict[str, Any]]
    resources: list[dict[str, Any]]
    scoring_policy: dict[str, Any]


class Snapshot(TypedDict):
    id: UUID
    created_at: datetime
    profile: Profile


def _fail(path: str, message: str) -> None:
    raise CoreError('VALIDATION_ERROR', f'{path}: {message}')


def _object(value: Any, required: set[str], optional: set[str], path: str) -> None:
    if not isinstance(value, dict) or set(value) - required - optional or required - set(value):
        _fail(path, 'Проверьте обязательные и неизвестные поля.')


def _text(value: Any, path: str, maximum: int = 128, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or re.search(r'[\x00-\x1f\ud800-\udfff]', value):
        _fail(path, f'Нужна непустая строка до {maximum} символов без управляющих знаков.')


def _decimal(value: Any, path: str, maximum: str = '1000000000000000000', nullable: bool = True, positive: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,19}(?:\.[0-9]{1,12})?', value):
        _fail(path, 'Нужна конечная неотрицательная десятичная строка (до 12 дробных знаков).')
    number = Decimal(value)
    if number > Decimal(maximum) or (positive and number == 0):
        _fail(path, 'Число вне допустимого диапазона.')


def _timestamp(value: Any, path: str) -> None:
    if value is None:
        return
    try:
        if not isinstance(value, str) or len(value) > 40 or datetime.fromisoformat(value).utcoffset() is None:
            raise ValueError()
    except ValueError:
        _fail(path, 'Нужна дата ISO с часовым поясом либо null.')


def _date(value: Any, path: str) -> None:
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError()
        date.fromisoformat(value)
    except ValueError:
        _fail(path, 'Нужна календарная дата YYYY-MM-DD.')


def _rows(value: Any, path: str) -> None:
    if not isinstance(value, list):
        _fail(path, 'Нужен список строк.')


def _catalog_row(row: dict, path: str) -> None:
    _text(row['id'], path+'.id')
    _text(row['name'], path+'.name', 2000)
    _text(row['unit'], path+'.unit', 40)
    if type(row['enabled']) is not bool:
        _fail(path+'.enabled', 'Нужен логический переключатель.')
    attrs = row['attributes']
    if not isinstance(attrs, dict) or len(attrs) > 100:
        _fail(path+'.attributes', 'Нужно до 100 строковых атрибутов.')
    for key, value in attrs.items():
        _text(key, path+'.attributes.key', 100)
        _text(value, path+'.attributes.'+key, 2000)


def validate_profile(value: Any) -> Profile:
    """Validate at application boundary and return an owned JSON-safe copy."""
    _object(value, set(Profile.__annotations__), set(), 'profile')
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, UnicodeError):
        _fail('profile', 'Профиль должен содержать только допустимые JSON значения.')
    try:
        UUID(value['stock_artifact_id'])
    except (ValueError, TypeError, AttributeError):
        _fail('stock_artifact_id', 'Нужен UUID складского файла.')
    if not isinstance(value['stock_sha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['stock_sha256']):
        _fail('stock_sha256', 'Нужна SHA-256 исходного файла.')
    if type(value['include_experimental']) is not bool:
        _fail('include_experimental', 'Нужен логический переключатель.')
    for key in ('products', 'experimental_products', 'production', 'resources'):
        _rows(value[key], key)
    ids, skus = set(), set()
    for index, row in enumerate(value['products']):
        path = f'products[{index}]'
        _object(row, {'id','sku','name','unit','attributes','enabled'}, {'manufactured_before','evidence'}, path)
        _catalog_row(row, path)
        _text(row['sku'], path+'.sku', 100, nullable=True)
        if row['id'] in ids or (row['sku'] is not None and row['sku'] in skus):
            _fail(path, 'Дублируется идентификатор или SKU.')
        ids.add(row['id'])
        if row['sku'] is not None:
            if row['sku'].startswith(('EXP:', 'CAT:')):
                _fail(path+'.sku', 'Префикс зарезервирован для виртуального каталога.')
            skus.add(row['sku'])
        if row.get('manufactured_before') is not None and type(row['manufactured_before']) is not bool:
            _fail(path+'.manufactured_before', 'Ожидается true, false или null.')
        if 'evidence' in row:
            _text(row['evidence'], path+'.evidence', 2000)
    experimental_ids = set()
    for index, row in enumerate(value['experimental_products']):
        path = f'experimental_products[{index}]'
        _object(row, {'id','name','unit','attributes','enabled'}, {'product_id','sku'}, path)
        _catalog_row(row, path)
        _text(row.get('sku'), path+'.sku', 100, nullable=True)
        if row['id'] in experimental_ids:
            _fail(path, 'Дублируется идентификатор.')
        experimental_ids.add(row['id'])
        _text(row.get('product_id'), path+'.product_id', nullable=True)
        if row.get('product_id') is not None and row['product_id'] not in ids:
            _fail(path+'.product_id', 'Продукт не найден в этом профиле.')
    groups = set()
    for index, row in enumerate(value['resources']):
        path = f'resources[{index}]'
        _object(row, {'group_id','calendar'}, {'default_daily_capacity','enabled','free_capacity_by_day','confirmed_at'}, path)
        _text(row['group_id'], path+'.group_id')
        if type(row.get('enabled', True)) is not bool:
            _fail(path+'.enabled', 'Нужен логический переключатель.')
        if row['group_id'] in groups:
            _fail(path, 'Дублируется группа ресурсов.')
        groups.add(row['group_id'])
        if (not isinstance(row['calendar'], list) or len(row['calendar']) > 7
                or any(type(day) is not int or day not in range(7) for day in row['calendar'])
                or len(set(row['calendar'])) != len(row['calendar'])):
            _fail(path+'.calendar', 'Нужны уникальные дни недели 0..6.')
        _timestamp(row.get('confirmed_at'), path+'.confirmed_at')
        days = row.get('free_capacity_by_day', {})
        if not isinstance(days, dict) or len(days) > 3660:
            _fail(path, 'Нужно до 3660 дат загрузки.')
        for day, capacity in days.items():
            _date(day, path+'.free_capacity_by_day')
            _decimal(capacity, path+'.free_capacity_by_day.'+day, '1', nullable=False)
        if 'default_daily_capacity' in row:
            _decimal(row['default_daily_capacity'], path+'.default_daily_capacity', '1', nullable=False)
            if not row['calendar']:
                _fail(path, 'Ежедневная доступность требует календаря.')
    product_ids = ids | experimental_ids
    pairs = set()
    for index, row in enumerate(value['production']):
        path = f'production[{index}]'
        _object(row, {'product_id','resource_group_id','units_per_workday','production_unit_cost','currency',
                      'setup_workdays','material_ready_at'}, {'confirmed_at','evidence','setup_cost'}, path)
        _text(row['product_id'], path+'.product_id')
        _text(row['resource_group_id'], path+'.resource_group_id')
        if row['product_id'] not in product_ids or row['resource_group_id'] not in groups:
            _fail(path, 'Не найдены продукт или группа ресурсов.')
        pair = row['product_id'], row['resource_group_id']
        if pair in pairs:
            _fail(path, 'Дублируется производственная возможность.')
        pairs.add(pair)
        _decimal(row['units_per_workday'], path+'.units_per_workday')
        _decimal(row['production_unit_cost'], path+'.production_unit_cost')
        _decimal(row.get('setup_cost'), path+'.setup_cost')
        _decimal(row['setup_workdays'], path+'.setup_workdays', '3660', nullable=False)
        if row['currency'] is not None and (not isinstance(row['currency'], str) or not re.fullmatch('[A-Z]{3}',row['currency'])):
            _fail(path+'.currency', 'Нужен трёхбуквенный код валюты.')
        if (row['production_unit_cost'] is not None or row.get('setup_cost') is not None) and row['currency'] is None:
            _fail(path, 'Себестоимость требует валюты.')
        for key in ('confirmed_at','material_ready_at'):
            _timestamp(row.get(key), path+'.'+key)
        if 'evidence' in row:
            _text(row['evidence'],path+'.evidence',2000)
        if row['units_per_workday'] is not None or row['production_unit_cost'] is not None or row.get('setup_cost') is not None:
            if not row.get('evidence'):
                _fail(path, 'Мощность и затраты требуют источник подтверждения.')
    _policy(value['scoring_policy'])
    return deepcopy(value)


def _policy(policy: Any) -> None:
    ratios = {'target_margin','comfortable_utilization','minimum_production_factor','stress_price_factor','stress_cost_factor','stress_rate_factor'}
    _object(policy, set(), ratios | {'version','high_score','medium_score','max_age_by_source'}, 'scoring_policy')
    if 'version' in policy:
        _text(policy['version'],'scoring_policy.version',64)
    for key in ratios & set(policy):
        _decimal(policy[key], 'scoring_policy.'+key, '10' if key.startswith('stress_') else '1', nullable=False)
        if key in {'target_margin','stress_rate_factor'} and Decimal(policy[key]) <= 0:
            _fail('scoring_policy.'+key, 'Значение должно быть больше нуля.')
        if key == 'comfortable_utilization' and Decimal(policy[key]) >= 1:
            _fail('scoring_policy.'+key, 'Комфортная загрузка должна быть меньше единицы.')
    for key in ('high_score','medium_score'):
        if key in policy and (type(policy[key]) is not int or not 0 <= policy[key] <= 100):
            _fail('scoring_policy.'+key, 'Нужен целый балл 0..100.')
    if policy.get('medium_score',0) > policy.get('high_score',100):
        _fail('scoring_policy','Средний порог не может превышать высокий.')
    if 'max_age_by_source' in policy:
        ages = policy['max_age_by_source']
        if not isinstance(ages,dict) or len(ages)>20:
            _fail('scoring_policy.max_age_by_source','Нужен словарь сроков свежести.')
        for key, age in ages.items():
            _text(key,'scoring_policy.max_age_by_source.key',100)
            _decimal(age,'scoring_policy.max_age_by_source.'+key,'315360000',nullable=True)


def active_products(profile: Profile) -> list[dict]:
    rows = [deepcopy(p) for p in profile['products'] if p['enabled']]
    rows.extend(deepcopy(p) for p in active_experimental_products(profile))
    return rows


def active_experimental_products(profile: dict) -> list[ExperimentalProduct]:
    if not profile.get('include_experimental'):
        return []
    return [p for p in profile.get('experimental_products', []) if p['enabled']]


def experimental_catalog_key(product: ExperimentalProduct) -> str:
    return 'EXP:' + product['id']


def production_products(profile: dict) -> dict[str, Product | ExperimentalProduct]:
    official = profile.get('products', [])
    official_ids = {p['id'] for p in official}
    products = {p.get('sku') or 'CAT:' + p['id']: p for p in official if p['enabled']}
    # Older snapshots allowed overlapping IDs. A capability under that ID
    # already belonged to the official product and must stay there.
    products.update({experimental_catalog_key(p): p for p in active_experimental_products(profile)
                     if p['id'] not in official_ids})
    return products


def search_phrases(profile: Profile) -> list[str]:
    result, seen = [], set()
    for row in active_products(profile):
        attrs = row['attributes']
        phrase = ' '.join((attrs.get('family') or attrs.get('group') or row['name']).split())
        # Split long source text without losing tokens; provider limit applies to each phrase.
        while phrase:
            end = 500 if len(phrase) > 500 else len(phrase)
            if end == 500 and ' ' in phrase[:500]:
                end = phrase.rfind(' ',0,501)
            part, phrase = phrase[:end], phrase[end:].lstrip()
            if part.casefold() not in seen:
                result.append(part)
                seen.add(part.casefold())
    return result


def stock_for_profile(stock: dict, profile: Profile, *, legacy_links: bool = False,
                      expose_experimental_sku: bool = True) -> dict:
    """Restrict physical stock, adding explicitly zero rows for catalog-only entries."""
    selected = {p['id'] for p in profile['products'] if p['enabled']}
    experimental = active_experimental_products(profile)
    if legacy_links:
        selected.update(p['product_id'] for p in experimental if p.get('product_id') is not None)
    selected_products = [p for p in profile['products'] if p['id'] in selected]
    skus = {p['sku'] for p in selected_products if p['sku'] is not None}
    catalog = {p['sku']:p for p in selected_products if p['sku'] is not None}
    rows = []
    for source in stock['rows']:
        if source['sku'] in skus:
            row = deepcopy(source)
            product = catalog[source['sku']]
            row.update(product=product['name'], attributes=deepcopy(product['attributes']))
            rows.append(row)
    present = {row['sku'] for row in rows}
    for product in selected_products:
        sku = product['sku'] or 'CAT:'+product['id']
        if sku not in present:
            rows.append(dict(row=len(rows)+2,sku=sku,product=product['name'],unit=product['unit'],free_quantity='0',
                             unit_cost=None,currency=None,as_of=None,attributes=deepcopy(product['attributes'])))
            present.add(sku)
    for product in experimental:
        if not legacy_links or product.get('product_id') is None:
            attributes = deepcopy(product['attributes'])
            if expose_experimental_sku and not legacy_links and product.get('sku') is not None:
                # The matcher can cite the SKU, while allocations retain their distinct catalog key.
                attributes['sku'] = product['sku']
            rows.append(dict(row=len(rows)+2,sku=experimental_catalog_key(product),product=product['name'],
                             unit=product['unit'],free_quantity='0',unit_cost=None,currency=None,as_of=None,
                             attributes=attributes))
    return dict(stock, rows=rows)


